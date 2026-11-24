"""生成：把用户的编辑（图层摆放）编译成一次 H3 生成。

粗剪（空区涂灰 128）+ 重绘区 + 参考图 + 提示词 → 引擎 → 成片（整幅用生成输出）。
"""
import json
import random
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from engine import check_cancel
from plate import pano_to_frame
from project import FFMPEG, read_video, write_video
import panorama as PANO
import mentions as MN
import roughcut
from engine import h3_canvas
from regions import explicit_mask, BODY, EMPTY, TRANS_T, disk, keepout, redrawn, temporal_grow, trans_opts

# 关键帧钉帧（MiniMaxH3AddGuide）：默认关（2026-11-23 用户定）。5 个案例里钉帧只让那一帧略近一点，
# 还可能在钉住的那一帧附近跳一下（53b7、2dd6）；关键帧只当 <Picture N> + 提示词写秒数。
KEY_PIN = False


@dataclass
class Pass:
    label: str
    input_video: Path
    mask: np.ndarray                     # (n, h, w) bool
    prompt: str
    denoise: float = 1.0
    refs: list = field(default_factory=list)
    ref_labels: list = field(default_factory=list)   # 每张参考图是什么（首帧 / 尾帧 / 背景图 / 资料卡…），写进 inputused.txt
    audio: object = None                 # 原片（取音轨），和粗剪一起编码，声音不重绘
    partial: object = None               # 主体重绘强度：(像素 (n,h,w) bool, 强度 0~1)；None = 本体不重绘（默认）
    protect: object = None               # 保护脸部：像素 (n,h,w) bool，含这些像素的 H3 格子整格不重绘；None = 不保护
    hole: object = None                  # 保护脸部时一起传：空区像素 (n,h,w) bool，含空区的格子不保护（节点 hole_mask）
    seed: int = field(default_factory=lambda: random.randint(0, 2**31 - 1))
    pins: list = field(default_factory=list)     # 关键帧钉帧：[(参考图下标, 视频帧号)]，同一张图既是 <Picture N> 又钉在这一帧（MiniMaxH3AddGuide）


def grow(masks, px):
    if px <= 0:
        return masks.copy()
    k = disk(px)
    return np.stack([cv2.dilate(m.astype(np.uint8), k) > 0 if m.any() else m for m in masks])


def extra_refs(project, ids):
    """「其他图片」：视频生成栏里勾上的那些（ids = 项目里 gen_refs 的 id，顺序照勾上的顺序）。返回 [(名字, 路径, id)]。"""
    import images as IM
    out = []
    for rid in ids or []:
        r = next((x for x in IM.gen_refs(project) if x["id"] == rid), None)
        f = IM.gen_ref_path(project, rid) if r else None
        if f and f.exists():
            out.append((r.get("label") or "其他图片", f, rid))
    return out


def prefill(project, c, comp, zones, rect, log):
    """背景预填图层（regions._Layout.prefill）：它们范围里、帧范围内、本来就是空区的像素，粗剪里用背景图
    （全景图按这一帧的镜头变换）代替灰 128。仍然重绘（mask 不变）。改 comp（原地），返回预填过的像素（n×H×W bool），
    没有预填图层或没有背景图返回 None。"""
    from regions import EMPTY, to_frame
    L = c.get("layout")
    objs = getattr(L, "prefill", None) or []
    hp = [o for o in getattr(L, "objs", []) if o.get("hole_prefill")]   # 图层勾了「原位置预填背景」：它造成的空区（原位置的洞等）也预填
    if not objs and not hp:
        return None
    pano = PANO.load(project)
    if pano is None:
        log("  背景预填：还没有背景图（图片编辑 → 背景图，先提取再生成），这次照旧涂灰")
        return None
    n, H, W = zones.shape
    cr = project.crop_rows()
    oy = rect[1] - cr[0] if cr else rect[1]              # 全景图坐标是裁切黑边后的
    masks = [project.masks(o["id"]) for o in objs]       # 原视频大小，每帧
    keep = np.zeros((n, H, W), bool)
    hw = (project.data["h"], project.data["w"])
    for i in range(n):
        m = np.zeros(hw, bool)
        for a in masks:
            m |= a[i]
        if hp:
            by = L.holes_by_layer(i)
            for o in hp:
                if o["id"] in by:
                    m |= by[o["id"]]
        if not m.any():
            continue
        k = to_frame(m, rect, False) & (zones[i] == EMPTY)
        if not k.any():
            continue
        bg = pano_to_frame(pano["img"], pano["T"], pano["Hs"][min(i, len(pano["Hs"]) - 1)], (W, H),
                           offset=(rect[0], oy), border=(128, 128, 128))
        comp[i][k] = bg[k]
        keep[i] = k
    tot = max(int((zones == EMPTY).sum()), 1)
    log(f"  背景预填（{'、'.join([o['name'] for o in objs] + [o['name'] + ' 原位置' for o in hp])}）：空区里 {keep.sum() / tot * 100:.0f}% 用背景图填，其余照旧涂灰")
    return keep if keep.any() else None


EDGE_FEATHER = 32      # 羽化宽度默认值：H3 画布像素（一格）
EDGE_SAMPLE = 3        # 取色点离原视频边几像素（最边上那行常被编码压暗）
EDGE_COVER = 2         # 原视频最外这几像素（不是本体 / 空区的）也换成取到的颜色：盖掉压暗的边行


def feather_opts(project):
    """项目设置（蒙版面板「边界融合（全局）」，存在 trans 里）：(画框边缘羽化宽度, 空洞 / 人物边羽化宽度)，0 = 关。
    画框边缘默认开、空洞 / 人物边默认关（2026-11-23 用户定），宽度默认 EDGE_FEATHER。"""
    t = project.data.get("trans") or {}
    edge = int(t.get("edge_px", EDGE_FEATHER)) if t.get("edge_feather", True) else 0
    hole = int(t.get("hole_px", EDGE_FEATHER)) if t.get("hole_feather", False) else 0
    return edge, hole


def edge_feather(project, comp, zones, rect, log, keep=None, px=EDGE_FEATHER):
    """画框边缘羽化（默认开；2026-11-23，130c 扩画幅那条横缝）：画框外的灰带里挨着原视频的
    px 像素（H3 画布），改成原视频边上的颜色往外拉、线性渐变到灰 128。只动画框外、本来就是空区（灰）、
    没被背景预填的像素，移出原片的人（本体）不动；仍然重绘（mask 不变）。改 comp（原地），返回羽化过的像素
    （n×H×W bool，给 <Video 1> 别再涂回灰），画框没超出原视频时返回 None。"""
    from regions import EMPTY
    n, H, W = zones.shape
    x0, y0 = -rect[0], -rect[1]                          # 原视频在画框里的位置（半开区间）
    x1, y1 = x0 + project.data["w"], y0 + project.data["h"]
    cr = project.crop_rows()
    if cr:                                               # 裁切黑边：黑边那几行也算原视频外
        y0, y1 = max(y0, y0 + cr[0]), min(y1, y0 + cr[1])
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, W), min(y1, H)
    if x1 - x0 < 4 * EDGE_SAMPLE or y1 - y0 < 4 * EDGE_SAMPLE or (x0, y0, x1, y1) == (0, 0, W, H):
        return None
    d = px * H / h3_canvas(W, H)[1]                      # H3 画布像素 → 画框像素
    ys, xs = np.mgrid[0:H, 0:W]
    dx = np.maximum(np.maximum(x0 - xs, xs - (x1 - 1)), 0)
    dy = np.maximum(np.maximum(y0 - ys, ys - (y1 - 1)), 0)
    dist = np.sqrt(dx * dx + dy * dy)
    out_band = (dist > 0) & (dist <= d)
    inner = np.zeros((H, W), bool)                       # 原视频最外 EDGE_COVER 像素，只在挨着画框外的那几条边
    if y0 > 0: inner[y0:y0 + EDGE_COVER, x0:x1] = True
    if y1 < H: inner[y1 - EDGE_COVER:y1, x0:x1] = True
    if x0 > 0: inner[y0:y1, x0:x0 + EDGE_COVER] = True
    if x1 < W: inner[y0:y1, x1 - EDGE_COVER:x1] = True
    w = np.where(inner, 1.0, np.clip(1 - dist / (d + 1), 0, 1)).astype(np.float32)[..., None]
    sy = np.clip(ys, y0 + EDGE_SAMPLE, y1 - 1 - EDGE_SAMPLE) - y0   # 每个像素取色的点（原视频里、离边 EDGE_SAMPLE）
    sx = np.clip(xs, x0 + EDGE_SAMPLE, x1 - 1 - EDGE_SAMPLE) - x0
    done = np.zeros((n, H, W), bool)
    for i in range(n):
        z = zones[i]
        m = (out_band & (z == EMPTY)) | (inner & (z != EMPTY) & (z != BODY))
        if keep is not None:
            m &= ~keep[i]
        if not m.any():
            continue
        c = cv2.blur(comp[i][y0:y1, x0:x1], (15, 15), borderType=cv2.BORDER_REPLICATE)[sy, sx].astype(np.float32)
        v = np.clip(128 + w * (c - 128), 0, 255).astype(np.uint8)
        comp[i][m] = v[m]
        done[i] = m
    if not done.any():
        return None
    log(f"  画框边缘羽化：画框外挨着原视频的 {d:.0f} 像素（H3 画布 {px}）用边上的颜色渐变到灰")
    return done


def hole_feather(comp, zones, log, keep=None, px=EDGE_FEATHER):
    """空洞 / 人物边羽化（默认关；2026-11-23 试验 promo2 人边的描边没了）：空区里还是灰的像素（没被背景预填 /
    画框边缘羽化过），离最近的非空区像素（背景、人都算）px 像素（H3 画布）以内的，改成那个像素附近的颜色
    （只在画面里 9×9 模糊，不混进灰）线性渐变到灰 128。仍然重绘（mask 不变）。改 comp（原地），返回羽化过的像素。"""
    from regions import EMPTY
    n, H, W = zones.shape
    d = px * H / h3_canvas(W, H)[1]
    done = np.zeros((n, H, W), bool)
    for i in range(n):
        empty = zones[i] == EMPTY
        grey = empty if keep is None else empty & ~keep[i]
        src = ~empty
        if not grey.any() or not src.any():
            continue
        dist, lab = cv2.distanceTransformWithLabels(empty.astype(np.uint8), cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
        m = grey & (dist <= d)
        if not m.any():
            continue
        ys, xs = np.nonzero(src)                         # 标签 k（从 1 起）= 第 k 个非空区像素（按行扫描）
        s = src.astype(np.float32)
        blur = cv2.blur(comp[i].astype(np.float32) * s[..., None], (9, 9)) / np.maximum(cv2.blur(s, (9, 9)), 1e-3)[..., None]
        c = blur[ys, xs][lab[m] - 1]
        w = (1 - dist[m] / (d + 1))[:, None]
        comp[i][m] = np.clip(128 + w * (c - 128), 0, 255).astype(np.uint8)
        done[i] = m
    if not done.any():
        return None
    log(f"  空洞 / 人物边羽化：空区里挨着画面的 {d:.0f} 像素（H3 画布 {px}）用旁边的颜色渐变到灰")
    return done


def face_protect(project, c, zones, rect, log):
    """保护脸部（图层勾了 face_protect、识别过 face_mask；只管看得见的图层，背景里的人不管）：脸的像素按图层变换摆到画框里，
    只留本体（BODY）。交给 ReframeH3InpaintMask 的 protect_mask + conflict=protect：含脸的 H3 格子整格不重绘，
    嘴就是原视频的。和 zones 同大小，没有要保护的脸返回 None。"""
    L = c.get("layout")
    shown = getattr(L, "shown", []) or []
    for o in shown:
        if o.get("face_protect") and not o.get("face_mask"):
            log(f"  {o['name']}：勾了保护脸部但还没识别脸，这次没保护（在图层的「保护脸部」重新勾一次）")
    objs = [o for o in shown if o.get("face_protect") and o.get("face_mask")]
    if not objs:
        return None
    n, H, W = zones.shape
    Tm = np.array([[1, 0, -rect[0]], [0, 1, -rect[1]], [0, 0, 1]], np.float64)
    out = np.zeros((n, H, W), bool)
    for o in objs:
        face = project.raw_masks_by_id(o["face_mask"])
        M = np.eye(3)
        M[:2] = project.layer_matrix(o)
        A = (Tm @ M)[:2].astype(np.float32)
        for i in range(min(n, len(face))):
            f = face[i] & L.core_i(o["id"], i)
            if f.any():
                out[i] |= cv2.warpAffine(f.astype(np.uint8), A, (W, H), flags=cv2.INTER_NEAREST) > 0
    out &= zones == BODY
    if not out.any():
        return None
    log(f"  保护脸部（{'、'.join(o['name'] for o in objs)}）：{int(out.any(axis=(1, 2)).sum())} 帧有脸，含脸的 H3 格子不重绘")
    return out


def partial_body(c, zones, rect):
    """「主体重绘强度」作用的像素：本体（BODY），保护蒙版画过的地方除外（用户明确要保留的）。和 zones 同大小。"""
    from regions import to_frame
    body = zones == BODY
    L = c.get("layout")
    prot = getattr(L, "protect", None) or []
    if prot:
        full = [0, 0, L.w, L.h]
        for i in range(len(body)):
            pm = np.zeros((L.h, L.w), bool)
            for o in prot:
                pm |= L.mask(o["id"], i)
            body[i] &= ~(to_frame(pm, rect, False) if list(rect) != full else pm)
    return body


COLOR_PTS = 4000                         # 颜色校正：每帧最多取这么多参考点


def color_reference(comp, changed, cell_px, seed=0):
    """颜色校正的参考点：不重绘的地方（避开重绘格一圈、前后 4 帧）在合成输入里的真实颜色。
    返回 dict(ys, xs = 每帧一组 0~1 的坐标, rgb = 每帧 (k, 3) uint8)；参考点太少的帧为空。"""
    n, H, W = changed.shape
    k = np.ones((int(cell_px) * 2 + 1,) * 2, np.uint8)
    rng = np.random.default_rng(seed)
    ys, xs, rgb = [], [], []
    for i in range(n):
        bad = changed[max(0, i - 4):i + 5].any(0)       # H3 按 1 / 4 帧一组重绘：前后几帧有重绘，这一帧也可能被改
        ok = cv2.erode((~bad).astype(np.uint8), k) > 0  # 格里有一点重绘就整格重绘：离远一点
        yy, xx = np.nonzero(ok)
        if len(yy) < 300:
            ys.append(np.zeros(0, np.float32)); xs.append(np.zeros(0, np.float32)); rgb.append(np.zeros((0, 3), np.uint8))
            continue
        pick = rng.choice(len(yy), min(COLOR_PTS, len(yy)), replace=False)
        yy, xx = yy[pick], xx[pick]
        ys.append(((yy + 0.5) / H).astype(np.float32)); xs.append(((xx + 0.5) / W).astype(np.float32))
        rgb.append(comp[i][yy, xx])
    return dict(ys=ys, xs=xs, rgb=rgb)


def _ref_diff(frames, ref):
    """每帧 参考颜色 − 成片颜色 的中位数（每通道）；没有参考点的帧 = nan。"""
    gh, gw = frames.shape[1:3]
    d = np.full((len(frames), 3), np.nan, np.float32)
    for i in range(min(len(frames), len(ref["rgb"]))):
        if not len(ref["rgb"][i]):
            continue
        y = np.minimum((ref["ys"][i] * gh).astype(int), gh - 1)
        x = np.minimum((ref["xs"][i] * gw).astype(int), gw - 1)
        d[i] = np.median(ref["rgb"][i].astype(np.float32) - frames[i][y, x].astype(np.float32), 0)
    return d


def color_fix(out, ref, log):
    """整条偏色（2026-11-21 用户要）：H3 输出（VAE 解码、转存）让没重绘的地方也整体变暗 / 偏色（实测亮度约 −3.6/255，
    换背景时人还会偏黄）。以不重绘的地方为标准：每帧每通道算偏移（中位数，前后 15 帧平滑），整帧加上。只平移、不拉伸；
    有参考点的帧不到 5% 就整条跳过（几乎全重绘时没有可靠的标准）。原地改 out，返回每通道平均偏移（没校返回 None）。"""
    d = _ref_diff(out, ref)
    good = ~np.isnan(d[:, 0])
    if good.mean() < 0.05:
        log("  颜色校正：不重绘的地方太少，没有可靠的标准，跳过")
        return None
    idx = np.arange(len(d))
    for ch in range(3):                                  # 没有参考点的帧：用前后有参考点的帧插值
        d[:, ch] = np.interp(idx, idx[good], d[good, ch])
    k = 15
    sm = np.stack([np.convolve(np.pad(d[:, ch], k // 2, mode="edge"), np.ones(k) / k, "valid") for ch in range(3)], 1)
    sm = np.clip(sm, -20, 20).astype(np.float32)
    for i in range(len(out)):
        out[i] = np.clip(out[i].astype(np.float32) + sm[i], 0, 255).astype(np.uint8)
    return sm.mean(0)


def op_compose(project, engine, op, log):
    """生成：粗剪 + 重绘区 + 参考图（首帧 / 尾帧 / 背景图 / 资料卡，勾了才传）+ 补瑕疵提示词 → H3 → 成片放进「结果」。"""
    x = prepare_compose(project, engine, op, log)
    work, p, guide, mask, canvas_mask, W, H, n, c = (x[k] for k in ("work", "pass", "guide", "mask", "canvas_mask", "W", "H", "n", "c"))
    lifted = c["lifted"]
    log(f"③ H3 生成 {W}x{H}：重绘范围占画面 {mask.mean() * 100:.1f}%，参考图 {len(p.refs)} 张，粗剪视频 <Video 1>")
    meta = dict(project.data, w=W, h=H)                  # H3 画布按这次生成的大小（扩图时是画框）定
    levels = [(canvas_mask if canvas_mask is not None else mask, 255)]
    pass2 = getattr(p, "pass2", None)
    p.pass2 = None                                       # 一采；开了二采时先把一采存成一条结果（标「一采」），再跑二采
    ref = None
    if op.get("color_fix", True) is not False:           # 颜色校正（视频生成面板，默认开）：参考 = 不重绘的地方在合成输入里的颜色
        changed = mask | p.partial[0] if p.partial else mask
        ref = color_reference(c["comp"], changed, 1.5 * 32 * W / h3_canvas(W, H)[0], seed=p.seed)
    first = engine.inpaint_levels(p, work, meta, levels, log, ref_videos=[guide])
    name = "合成 " + "、".join(o["name"] for o in lifted)
    if not pass2 or not hasattr(engine, "second_pass"):    # 假引擎没有二采
        return save_result(project, work, first, n, c["base"], name, log, ref=ref)
    p.pass2 = pass2
    save_result(project, work, first, n, c["base"], "一采 · " + name, log, src="result_pass1.mp4", rid=work.name + "_p1", tag="pass1",
                ref=ref)
    final = engine.second_pass(p, work, meta, levels, first, log, ref_videos=[guide])
    if Path(final) == Path(first):                       # 二采画布不比一采大、跳过了：一采就是成片，不再存第二条
        return dict(open_result=work.name + "_p1")
    return save_result(project, work, final, n, c["base"], name, log, ref=ref)


def save_result(project, work, video, n, base, name, log, src="result.mp4", rid=None, tag=None, ref=None):
    """H3 输出整幅直接用，不贴回原片；成片 = H3 画布的分辨率（2026-11-15 用户定：去掉「放大到画幅尺寸」）。
    补帧多出来的按原片帧数取（不够就按时间比例拉），带原视频的声音，记进「视频结果」。
    ref = color_reference(...)（「颜色校正」开着）：按不重绘的地方把整条偏色校回来（color_fix）；写完读回来再量一次，
    把转存（RGB → yuv420）带来的偏差也补掉。"""
    g, _ = read_video(video)
    gh, gw = g.shape[1:3]
    out = np.empty((n, gh, gw, 3), np.uint8)
    for i in range(n):
        out[i] = g[i if len(g) >= n else min(round(i * len(g) / n), len(g) - 1)]
    del g                                                # 长片段 + 二采时一份就 1 GB 多：用完就放
    log(f"  成片 {gw}x{gh}（H3 输出的分辨率）")
    shift = color_fix(out, ref, log) if ref is not None else None
    write_video(work / src, out, project.data["fps"], audio_from=project.video("v0"))
    if shift is not None:
        back, _ = read_video(work / src)
        left = np.nanmean(_ref_diff(back, ref), 0)
        del back
        if np.isfinite(left).all() and np.abs(left).max() > 0.3:       # 转存的偏差（实测约 −0.8）：整条再补一次
            for i in range(len(out)):                    # 逐帧原地加，不整条复制成 float
                out[i] = np.clip(out[i].astype(np.float32) + left, 0, 255).astype(np.uint8)
            write_video(work / src, out, project.data["fps"], audio_from=project.video("v0"))
            back, _ = read_video(work / src)
            left = np.nanmean(_ref_diff(back, ref), 0)
            del back
        log(f"  颜色校正：不重绘的地方原来差 RGB {np.round(shift, 1).tolist()}，校完剩 {np.round(left, 1).tolist()}")
    return add_result(project, work, gw, gh, base, name, log, src=src, rid=rid, tag=tag)


def prepare_compose(project, engine, op, log, build_prompt=True, mention_fmt=None):
    """准备一次视频生成要交给 H3 的全部素材（粗剪、重绘区、<Video 1>、参考图、提示词），不调 H3。
    生成（op_compose）和「导出」（server.export_video）共用。返回 dict(work, pass, guide, mask, canvas_mask, W, H, n, c, keep)。
    build_prompt=False（视频生成走 API）：不问 Qwen-VL 写提示词（不碰 4090），提示词 = op["prompt"] 原样。
    mention_fmt = (图片写法, 视频写法)：「@名称」换成什么；不给 = H3（build_prompt）/ 云端视频的写法。"""
    c = roughcut.build(project, op, log)
    work, comp, zones, frames = c["work"], c["comp"], c["zones"], c["frames"]
    n = frames.shape[0]
    H, W = comp.shape[1:3]                               # 生成大小：扩图时是画框大小
    rect = c["rect"]
    shown, lifted, masks = c["shown"], c["lifted"], c["masks"]
    import images as IM
    use_cards = op.get("cards")                          # 视频生成栏里勾的资料卡（图层 id）；不传 = 挑了的都用
    cards = []                                           # 参考图-资料卡：画面里露出过的物体，挑了资料卡就传
    for o in shown:
        m = masks[o["id"]]
        others = np.zeros_like(m)
        for x in shown:
            if x is not o:
                others |= masks[x["id"]]
        if not (m & ~grow(others, 30)).any():
            continue
        if use_cards is not None and o["id"] not in use_cards:
            continue
        card = IM.picked_path(project, "card", o["id"]) or next(
            (IM.picked_path(project, "card", it["oid"]) for it in IM.items(project)
             if it["kind"] == "card" and it.get("pick") and project.obj(it["oid"])["name"] == o["name"]), None)
        if card:
            cards.append((o["id"], card))
            log(f"  {o['name']}：用资料卡当参考图（{card}）")
    pics, pic_labels, ends = [], [], []
    for end, i in (("first", 0), ("last", n - 1)):
        want = (op.get("anchors") or {}).get(end)        # 视频生成栏的勾选：True = 用（图层改过也用），False = 不用，不传 = 过期就不用
        if want is False:
            log(f"  不传{IM.KINDS[end]}（没勾）")
            continue
        f = IM.picked_path(project, end, vid=c["base"])
        stale = f and not IM.sig_close(IM.pick_sig(IM.find(project, end, vid=c["base"])), IM.layer_sig(project, c["base"]))   # 只挪了几个像素不算改过
        if stale and want is True:
            log(f"  {IM.KINDS[end]}是按旧的图层摆放取的，勾了照样用")
        elif stale:
            log(f"  {IM.KINDS[end]}是按旧的图层摆放取的，这次没用上；请在{IM.KINDS[end]}标签里点「重新取画面」再挑一张")
            continue
        if not f:
            continue
        a = Image.open(f)
        if a.size != (W, H):                             # 和这次生成的大小（画框）对不上：画框改过了
            log(f"  {IM.KINDS[end]}是 {a.size[0]}x{a.size[1]}，这次按 {W}x{H} 生成，对不上，没用上；"
                f"请在{IM.KINDS[end]}标签里点「重新取画面」")
            continue
        pics.append(f)
        pic_labels.append(IM.KINDS[end])
        ends.append((end, f, i))
        log(f"  {IM.KINDS[end]}（只当参考图，不钉进粗剪）：{f}")
    pins, key_ids = [], []                               # 关键帧（可以多张）：当 <Picture N>；KEY_PIN 开时同时钉在它那一帧
    for it in IM.keys(project, c["base"]):
        i = IM.still_index(project, it)
        name = f"关键帧（第 {i} 帧）"
        want = (op.get("anchors") or {}).get(f"key:{it['id']}")
        if want is False:
            log(f"  不传{name}（没勾）")
            continue
        f = IM.item_pick_path(project, it)
        if not f:
            continue
        if not IM.sig_close(IM.pick_sig(it), IM.layer_sig(project, c["base"])):
            if want is True:
                log(f"  {name}是按旧的图层摆放取的，勾了照样用")
            else:
                log(f"  {name}是按旧的图层摆放取的，这次没用上；请在这个关键帧标签里点「重新取画面」再挑一张")
                continue
        a = Image.open(f)
        if a.size != (W, H):
            log(f"  {name}是 {a.size[0]}x{a.size[1]}，这次按 {W}x{H} 生成，对不上，没用上；请在这个关键帧标签里点「重新取画面」")
            continue
        if KEY_PIN:
            pins.append((len(pics), i))
        pics.append(f)
        pic_labels.append(name)
        ends.append(("key", f, i))
        key_ids.append(it["id"])
        log(f"  {name}：当参考图，并钉在第 {i} 帧：{f}" if KEY_PIN else f"  {name}：当参考图（不钉帧）：{f}")
    bg_ref = None
    pano = PANO.load(project)
    if pano is not None and op.get("use_bg", True):      # 参考图-背景图：全景图变换到中间帧
        bg_ref = work / "bg_ref.png"
        if (project.data.get("panorama") or {}).get("mode"):
            Image.fromarray(pano["img"]).save(bg_ref)
        else:
            cr = project.crop_rows()
            oy = rect[1] - cr[0] if cr else rect[1]        # 全景图坐标是裁切黑边后的，要减去上黑边
            Image.fromarray(pano_to_frame(pano["img"], pano["T"], pano["Hs"][n // 2], (W, H), offset=(rect[0], oy), border=(128, 128, 128))).save(bg_ref)
        log(f"  使用所选背景参考图：{bg_ref}")
    extras = extra_refs(project, op.get("extra_refs"))    # 其他图片：用户自己上传的额外参考图
    for label, f, _ in extras:
        log(f"  {label}：当参考图（{f}）")
    # 用户提示词 / 系统提示词里的「@名称」→ 这次实际的图号（mentions.py）：顺序 = 首帧、尾帧、关键帧、背景图、资料卡、其他图片（跳过的不算）
    keys = [e for e, _, _ in ends if e != "key"] + [f"key:{k}" for k in key_ids] + (["bg"] if bg_ref else []) + [f"card:{oid}" for oid, _ in cards] + [f"other:{rid}" for _, _, rid in extras]
    mentions = MN.parse(op.get("mentions"))
    pic, vid_fmt = mention_fmt or ((MN.PICTURE_H3, MN.VIDEO_H3) if build_prompt else (MN.PICTURE_API_VIDEO, MN.VIDEO_API_VIDEO))
    user_prompt = MN.resolve(str(op.get("prompt") or ""), mentions, keys, pic, vid_fmt, log=log)
    system = MN.resolve(op.get("system"), mentions, keys, pic, vid_fmt, log=log) if op.get("system") else op.get("system")
    strength = min(max(float(op.get("body_redraw") or 0), 0.0), 1.0)   # 主体重绘强度（视频生成面板，默认 0 = 本体不重绘）
    if build_prompt:
        from repair_prompt import build
        prompt = build(project, engine, work, zones, ends, bg_ref is not None,
                       [oid for oid, _ in cards], None, user_prompt, log, comp,
                       layout=c["layout"], rect=rect, system=system, vid=c["base"], vl=op.get("vl", True) is not False,
                       vl_model=op.get("vl_model"), relight=strength > 0)
    else:
        prompt = user_prompt.strip()
    keep = prefill(project, c, comp, zones, rect, log)   # 背景预填图层：范围里的空区改用背景图
    edge_px, hole_px = feather_opts(project)             # 羽化（蒙版面板「边界融合（全局）」，项目设置）
    if edge_px:
        fe = edge_feather(project, comp, zones, rect, log, keep, edge_px)
        if fe is not None:
            keep = fe if keep is None else keep | fe
    if hole_px:
        fe = hole_feather(comp, zones, log, keep, hole_px)
        if fe is not None:
            keep = fe if keep is None else keep | fe
    write_video(work / "composite.mp4", comp, project.data["fps"])
    Image.fromarray(comp[0]).save(work / "frame_first.png")
    guide = roughcut.guide_video(comp, zones, work, project.data["fps"], keep)
    log(f"  <Video 1> 粗剪（空区涂灰）：{guide}")
    settings = project.data.get("trans") or {}
    canvas_mask = None
    if "frames" in settings or settings.get("blue"):
        canvas_mask = explicit_mask(zones, h3_canvas(W,H), settings.get("frames",0), c["layout"])
        mask = np.stack([cv2.resize(m.astype(np.uint8),(W,H),interpolation=cv2.INTER_NEAREST)>0 for m in canvas_mask])
    else:
        mask = redrawn(zones)                                # 本体（BODY）和保留区一样不重绘
        tg, ta = trans_opts(project)                         # 时间外扩：前后几帧也重绘，避开每帧本体周围的禁区
        if tg > 0:
            av = int(round(ta * W / h3_canvas(W, H)[0]))
            mask |= temporal_grow(mask, TRANS_T) & ~np.stack([keepout(z == BODY, av) for z in zones])
    p = Pass("合成", work / "composite.mp4", mask, prompt, 1.0,
             refs=pics + ([bg_ref] if bg_ref else []) + [Path(f) for _, f in cards] + [f for _, f, _ in extras],
             seed=int(op.get("seed", 42)), pins=pins,
             ref_labels=pic_labels + (["背景图"] if bg_ref else []) + [f"资料卡：{project.obj(oid)['name']}" for oid, _ in cards]
             + [label for label, _, _ in extras])
    p.audio = project.video("v0")                        # 原片音轨：和粗剪一起编码，声音不重绘
    if strength > 0:
        part = partial_body(c, zones, rect) & ~mask
        if part.any():
            p.partial = (part, strength)
            log(f"  主体重绘强度 {strength:.2f}：本体（保护蒙版除外）轻度重绘，占画面 {part.mean() * 100:.1f}%")
    face = face_protect(project, c, zones, rect, log)    # 保护脸部：含脸的 H3 格子整格不重绘（engine 传 protect_mask）
    if face is not None:
        p.protect = face
        p.hole = zones == EMPTY                          # 含空区（灰）的格子不保护，免得把灰一起留下
        if p.partial:                                    # 脸也不做主体重绘强度
            p.partial = (p.partial[0] & ~face, p.partial[1])
    p2 = op.get("pass2")                                # 二次采样（视频生成面板「二次采样（高清）」，默认关）：engine.second_pass
    if isinstance(p2, dict) and p2.get("on"):
        from engine import H3_PRESETS
        pre = str(p2.get("preset") or "1080")
        p.pass2 = dict(preset=pre if pre in H3_PRESETS else "1080", denoise=float(min(max(float(p2.get("denoise", 0.5)), 0.05), 1.0)))
    (work / "pass.txt").write_text(f"合成 {W}x{H}\nprompt:\n{prompt}\nrefs: {p.refs}\nref_videos: {[guide]}\n", encoding="utf-8")
    check_cancel()
    return dict(work=work, guide=guide, mask=mask, canvas_mask=canvas_mask, W=W, H=H, n=n, c=c, keep=keep,
                user_prompt=user_prompt.strip(), **{"pass": p})


# ---------- 修改项 → 提示词（和统一编辑 LoRA 的训练数据同一套写法，lora_unified/dataset_files/prompt_format.py）----------


def add_result(project, work, W, H, base, name, log, src="result.mp4", rid=None, tag=None):
    """生成结果记进项目（project.data["results"]），界面上作为一个「结果」标签打开播放。
    rid：结果 id，默认 = 生成文件夹名；二采时一采那条用「文件夹名_p1」，dir 记生成文件夹（导出靠它找素材），tag = "pass1"。"""
    import time
    rs = project.data.setdefault("results", [])
    k = max([r.get("k", 0) for r in rs if (r.get("base") or "v0") == base] or [0]) + 1   # 编号每个版本各自从 1 数
    f = work / src
    if project.v3:                                       # 结果result/<vN>/视频video/项目名_vN_K.mp4（硬链接，过程文件留在中间文件里）
        dst = project.version_results_dir(base, "视频video") / f"{project.out_name(base, k)}.mp4"
        while dst.exists():                              # 删过结果条目（文件还在）：编号往后让，绝不往已有文件里写（它可能是旧结果的硬链接）
            k += 1
            dst = dst.with_name(f"{project.out_name(base, k)}.mp4")
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            dst.hardlink_to(f)
        except OSError:
            import shutil
            shutil.copy2(f, dst)
        f = dst
    r = dict(id=rid or work.name, k=k, label=f"结果 {k}", name=name, file=str(f.relative_to(project.root)).replace("\\", "/"),
             w=W, h=H, base=base, time=time.strftime("%m-%d %H:%M"))
    if rid:
        r["dir"] = work.name
    if tag:
        r["tag"] = tag
    rs.append(r)
    project.save()
    try:                                                 # 过程文件去重：和以前的 pass 逐字节相同的换成硬链接（每个 pass 还是一份，但不占空间）
        n = project.dedupe_pass(work.name)
        if n:
            log(f"  过程文件去重：{n} 个和之前完全相同的文件换成硬链接，不额外占空间")
    except Exception as e:                               # 去重失败不影响结果
        log(f"  （过程文件去重跳过：{e}）")
    log(f"完成：{f}（放进「结果 {k}」标签；没有建新版本，也没有重新识别图层）")
    return dict(open_result=r["id"])


def api_check_duration(n, fps, lim):
    sec = n / fps
    if lim.get("min_s") and sec < lim["min_s"] - 1e-6:
        raise RuntimeError(f"这段只有 {sec:.1f} 秒，平台要求输入视频至少 {lim['min_s']} 秒")
    if lim.get("max_s") and sec > lim["max_s"] + 1e-6:
        raise RuntimeError(f"这段有 {sec:.1f} 秒，平台最多收 {lim['max_s']} 秒：先在分段页切短")


def api_video_input(comp, zones, keep, work, fps, lim, log):
    """视频生成走 API：粗剪（空区涂灰，规则和 <Video 1> 一样）按平台限制（video_limits）出一份原尺寸的 api_input.mp4。
    先在本机检查时长 / 宽高比（不合格直接报错，不上传）；像素不够就等比放大，帧率低于平台下限就补帧到下限（时长不变）。"""
    n, h, w = zones.shape
    api_check_duration(n, fps, lim)
    r = w / h
    if (lim.get("min_ratio") and r < lim["min_ratio"]) or (lim.get("max_ratio") and r > lim["max_ratio"]):
        raise RuntimeError(f"画幅宽高比 {r:.2f}，平台只收 {lim.get('min_ratio', 0)}–{lim.get('max_ratio', '∞')}：在「画幅」里改比例")
    s = max(1.0, ((lim.get("min_pixels") or 0) / (w * h)) ** 0.5, (lim.get("min_side") or 0) / min(w, h))
    if s > 1:
        log(f"  画面 {w}x{h} 低于平台下限，等比放大 {s:.2f} 倍再发")
    min_fps = lim.get("min_fps") or 0
    f = roughcut.guide_video(comp, zones, work, fps, keep, width=int(np.ceil(w * s / 2)) * 2,
                             name="api_input_src.mp4" if fps < min_fps else "api_input.mp4", crf=18)
    if fps < min_fps:                                    # 补帧到平台下限，时长不变（23.976 → 24 只差千分之一）
        log(f"  帧率 {fps:.3f} 低于平台下限，补帧到 {min_fps:g} fps（时长不变）")
        dst = Path(work) / "api_input.mp4"
        r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(f), "-vf", f"fps={min_fps}", "-an", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-crf", "18", str(dst)], capture_output=True)
        if r.returncode:
            raise RuntimeError(r.stderr.decode("utf-8", "replace")[-500:])
        f = dst
    mb = f.stat().st_size / 1e6
    if lim.get("max_mb") and mb > lim["max_mb"]:
        raise RuntimeError(f"粗剪视频 {mb:.0f} MB，超过平台上传上限 {lim['max_mb']} MB：先在分段页切短")
    return f


def op_api_video(project, engine, op, log):
    """视频生成走云端 API（2026-11-16 用户定，和 H3 那条路分开）：粗剪（空区涂灰）+ 视频生成栏勾选的参考图 + 用户写的提示词（原样发）
    → 用户在设置里配的平台（配置里要有 video_path）→ 成片放进「结果」。不碰 4090：不问 Qwen-VL、不跑 H3。"""
    api = getattr(engine, "api", None)
    if api is None:
        raise RuntimeError("当前引擎没有云端 API")
    pf = api.pf
    if not pf:
        raise RuntimeError("还没设云端平台：右上角设置 → 云端 API，按接口格式新建一个平台并填 key")
    if not pf.get("video_path"):
        raise RuntimeError(f"「{pf.get('name', api.cfg['provider'])}」这个平台的配置里没有 video_path，不能做视频生成：在设置 → 云端 API 的接口配置里补上")
    if not hasattr(api, "generate_video"):
        raise RuntimeError(f"「{pf.get('name', api.cfg['provider'])}」的接口格式是 {pf.get('format')}：视频生成只支持 async_task")
    if not str(op.get("prompt") or "").strip():
        raise RuntimeError("先写提示词")
    api._headers()                                       # 没填 key / 时长不合格就别先做粗剪
    api_check_duration(project.data["n"], project.data["fps"], pf.get("video_limits") or {})
    x = prepare_compose(project, engine, op, log, build_prompt=False)
    work, p, n, c = x["work"], x["pass"], x["n"], x["c"]
    fps = project.data["fps"]
    comp, zones = c["comp"], c["zones"]
    video = api_video_input(comp, zones, x["keep"], work, fps, pf.get("video_limits") or {}, log)
    for k, (f, lab) in enumerate(zip(p.refs, p.ref_labels), start=1):
        log(f"  @图片{k} = {lab}（{f}）")
    model = api.video_model
    (work / "api_request.json").write_text(json.dumps(dict(provider=api.cfg["provider"], model=model, resolution=api.video_resolution,
                                                          prompt=p.prompt, refs=[str(f) for f in p.refs], ref_labels=p.ref_labels,
                                                          video=video.name), ensure_ascii=False, indent=1), encoding="utf-8")
    raw = api.generate_video(video, p.refs, p.prompt, work / "api_out.mp4", log)
    g, _ = read_video(raw)
    OH, OW = g.shape[1:3]
    # 平台的输出跟输入时长走（帧率可能被补过），不跟帧号走：一律按时间比例对齐到原片的 n 帧
    out = np.stack([g[min(round(i * len(g) / n), len(g) - 1)] for i in range(n)])
    log(f"  成片 {OW}x{OH}（平台输出的分辨率），{len(g)} 帧按原片对齐成 {n} 帧")
    write_video(work / "result.mp4", out, fps, audio_from=project.video("v0"))
    return add_result(project, work, OW, OH, c["base"], f"API · {model} · 合成 " + "、".join(o["name"] for o in c["lifted"]), log)


def op_workflow_video(project, engine, op, log):
    """视频生成走用户自己的 ComfyUI 视频工作流（workflows/video/<名字>.json，2026-11-18 用户定）：素材和 H3 那条路同一份
    （prepare_compose：粗剪、重绘 mask、<Video 1>、勾选的参考图）；工作流里有 name = prompt 的提示词节点时才问 Qwen-VL 写完整提示词，
    name = user 的拿用户原文。成片按原片帧数对齐（多出来的尾帧截掉），带原片声音。不用 H3 的 LoRA / 步数 / 二采。"""
    import workflows as WF
    name = str(op.get("workflow") or "")
    try:
        wf = WF.load_video(name)
    except ValueError as e:
        raise RuntimeError(str(e))
    need = WF.needs_video(wf)
    if not hasattr(engine, "run_video_workflow"):
        raise RuntimeError("假引擎不能跑自己的视频工作流：启动时要连 ComfyUI")
    op = {k: v for k, v in op.items() if k != "pass2"}
    x = prepare_compose(project, engine, op, log, build_prompt="prompt" in need["texts"], mention_fmt=(MN.PICTURE_H3, MN.VIDEO_H3))
    work, p, n, c, W, H = x["work"], x["pass"], x["n"], x["c"], x["W"], x["H"]
    fps = project.data["fps"]
    log(f"③ 工作流「{name}」生成 {W}x{H}：重绘范围占画面 {x['mask'].mean() * 100:.1f}%，参考图 {len(p.refs)} 张")
    mask = x["canvas_mask"] if x["canvas_mask"] is not None else x["mask"]
    raw = engine.run_video_workflow(name, wf, p, work, (W, H), mask, x["guide"], {"prompt": p.prompt, "user": x["user_prompt"]}, log)
    g, _ = read_video(raw)
    OH, OW = g.shape[1:3]
    # 工作流自己补的帧在末尾：够 n 帧就取前 n 帧；不够（比如抽了帧）按时间比例对齐
    out = g[:n] if len(g) >= n else np.stack([g[min(round(i * len(g) / n), len(g) - 1)] for i in range(n)])
    log(f"  成片 {OW}x{OH}（工作流输出的分辨率），{len(g)} 帧对齐成 {n} 帧")
    write_video(work / "result.mp4", out, fps, audio_from=project.video("v0"))
    return add_result(project, work, OW, OH, c["base"], f"工作流 · {name} · 合成 " + "、".join(o["name"] for o in c["lifted"]), log)


def run_op(project, engine, op, log):
    check_cancel()
    if op.get("type") == "api_video":
        return op_api_video(project, engine, op, log)
    if op.get("type") == "compose" and op.get("workflow"):
        return op_workflow_video(project, engine, op, log)
    if op.get("type") != "compose":
        raise ValueError(f"未知操作 {op.get('type')}")
    return op_compose(project, engine, op, log)
