"""参考图-背景图：去掉所有物体后的空镜头背景。

整段视频的背景先拼成一张全景图（plate.panorama），物体身后整段都没露出过的地方和原片外，交给 Qwen 补；
生成时变换到中间帧当一张参考图。文件都在项目的 panorama/ 文件夹里。
"""
import io
import json
import time
import uuid
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


from plate import homographies, AlignmentError, panorama, pano_to_frame, smooth_homographies
from regions import disk, frame_rect, rect_blocks

PANO_SMOOTH = 2.0      # 全景图逐帧变换的时间平滑（帧）。0 = 不平滑；1009 上 2 最好（再大会和原片对不准）

# 第二步的默认提示词：只补背景、不要人、也不要任何动物（2026-11-13：只写 no people 时，雪地场景里 Qwen 会把
# 洞口补成一只毛茸茸的动物（854 是熊、1408 是狗），所以把「不要生物」写死，并点明洞里是什么）
DEFAULT_PROMPT = ("Fill the flat grey areas with the background so they continue the surrounding scene naturally, as if nothing "
                  "had ever been there. The grey areas are just empty ground, sky, walls or plants. "
                  "Keep everything else exactly as it is. Do not add any people. "
                  "Do not add any animals, creatures or fur.")
PE_ASK = (" These instructions are about the flat grey (missing) areas only: fill them with the background so they continue the "
          "surrounding real scene naturally. Do not add any people, silhouettes or reflections of people, and do not add any "
          "animals, creatures or fur.")
BG_WORK_MP = 1.0        # 交给 Qwen 补背景的工作尺寸：按约 100 万像素（Qwen Image 推荐的量级）自动换算，
BG_WORK_LONG = 0        # 长边不再写死（0 = 按像素数自动）；opts["long"] 仍可强制指定长边
BG_WORK_MIN = 64        # 每边至少这么多像素（和 32 的倍数一起用）
BG_WORK_STEP = 32       # Qwen 会把边长取整到 32 的倍数：先自己对齐，省得补边
BG_FEATHER = 6          # 补出来的洞贴回原底图时，边缘往洞外羽化几像素（原底图分辨率）


def paste_holes(orig, gen, hole, feather=BG_FEATHER):
    """只把洞里补出来的贴回原底图：原片拍到的像素保持原样（不再整张被缩小再放大弄糊）。
    贴之前按洞外一圈把补出来的颜色平移到和原片一致（Qwen 出图整体会偏一点色，不对齐会看到人形接缝），边缘往洞外羽化 feather 像素。"""
    gen = gen.astype(np.float32)
    ring = (cv2.dilate(hole.astype(np.uint8), disk(2 * feather + 9)) > 0) & ~hole
    if ring.sum() > 200:
        gen = gen + (orig[ring].astype(np.float32).mean(0) - gen[ring].mean(0))
    w = cv2.dilate(hole.astype(np.uint8), disk(feather)).astype(np.float32)
    w = np.maximum(cv2.GaussianBlur(w, (0, 0), max(feather / 2, 0.5)), hole)[..., None]
    return np.clip(orig * (1 - w) + gen * w, 0, 255).astype(np.uint8)


def folder(p):
    d = p.panorama_dir()
    d.mkdir(exist_ok=True)
    return d


MODES = {"frame": "指定帧", "layer": "背景图层", "panorama": "全景"}   # 背景提取模式 → 界面 / 历史里的名字
FRAME_RING, FRAME_ERR, FRAME_BAD = 48, 6.0, 0.1   # 指定帧补洞：局部细调用洞周围多宽一圈；那一圈亮度误差中位数上限；补上去那块外沿对不上的像素比例上限


def _local_affine(src, dst, mask, scale=0.5):
    """在 mask 范围里把 src 再对准 dst（ECC，仿射），返回全分辨率 3x3（src 坐标 → dst 坐标）。"""
    h, w = dst.shape
    lw, lh = round(w * scale), round(h * scale)
    a = cv2.resize(src, (lw, lh), interpolation=cv2.INTER_AREA).astype(np.float32)
    b = cv2.resize(dst, (lw, lh), interpolation=cv2.INTER_AREA).astype(np.float32)
    m = cv2.resize(mask.astype(np.uint8), (lw, lh), interpolation=cv2.INTER_NEAREST)
    _, A = cv2.findTransformECC(b, a, np.eye(2, 3, dtype=np.float32), cv2.MOTION_AFFINE,
                                (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5), m, 5)
    S = np.diag([scale, scale, 1.0])
    return np.linalg.inv(S) @ np.vstack([cv2.invertAffineTransform(A), [0, 0, 1]]) @ S


def _align_to(frames, fg, ref, log):
    """每帧 → 第 ref 帧的单应性（960 宽上算，换回原尺寸）和每帧是否可靠。"""
    n, h, w = fg.shape
    scale = min(1, 960 / w)
    sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
    small = np.stack([cv2.resize(f, (sw, sh), interpolation=cv2.INTER_AREA) for f in frames])
    excluded = np.stack([cv2.resize(m.astype(np.uint8), (sw, sh), interpolation=cv2.INTER_NEAREST) > 0 for m in fg])
    transform = np.diag([sw / w, sh / h, 1])
    small_matrices, reliable = homographies(small, excluded, ref, log, with_status=True)
    return np.linalg.inv(transform) @ small_matrices @ transform, reliable


def layer_background(frames, fg, ref, log):
    """「背景图层」：第 ref 帧原样，物体的地方留空（不从别的帧补，空的全交给 Qwen）。
    还是要算各帧对齐：之后把背景图按镜头变换回别的帧（PANO.to_frame）要用。"""
    matrices, _ = _align_to(frames, fg, ref, log)
    holes = fg[ref].copy()
    log(f"背景图层第 {ref} 帧：物体的地方留空，不从别的帧补")
    return dict(plate=frames[ref].copy(), holes=holes, valid=~holes, covered=np.ones_like(holes),
                weak=np.zeros_like(holes), ref=ref, size=(fg.shape[2], fg.shape[1]), T=np.eye(3), Hs=matrices)


def frame_background(frames, fg, ref, log):
    """以第 ref 帧为准：物体挡住的地方从别的帧补。镜头一动近处和远处错开（视差），整帧一个对齐对不上，
    所以（2026-11-07，f01d 柱子重影）：每块洞尽量整块从同一帧补（挑看得见这块最多的帧，不拼一条条细条）；
    补之前在洞周围一圈再局部对准一次；那一圈或补上去那块的外沿对不上（近处的柱子跑到洞后面）就换下一帧，都不行就留给 Qwen。"""
    n, h, w = fg.shape
    matrices, reliable = _align_to(frames, fg, ref, log)
    cand = [i for i in range(n) if i != ref and reliable[i]]
    q = 0.25                                              # 挑帧：低分辨率算每帧能看到参考帧里的哪些背景
    qw, qh = max(1, round(w * q)), max(1, round(h * q))
    Q = np.diag([qw / w, qh / h, 1.0])
    seen = {i: cv2.warpPerspective((~fg[i]).astype(np.uint8), Q @ matrices[i] @ np.linalg.inv(Q), (qw, qh), flags=cv2.INTER_NEAREST) > 0
            for i in cand}
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray = lambda x: clahe.apply(cv2.cvtColor(x, cv2.COLOR_RGB2GRAY))
    luma = lambda x: cv2.GaussianBlur(cv2.cvtColor(x, cv2.COLOR_RGB2GRAY), (0, 0), 2).astype(np.float32)
    ring_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * FRAME_RING + 1,) * 2)
    plate = frames[ref].copy()
    missing = fg[ref].copy()
    nl, lab = cv2.connectedComponents(cv2.dilate(missing.astype(np.uint8), np.ones((15, 15), np.uint8)))
    used, rejected = set(), 0
    for c in range(1, nl):
        comp = (lab == c) & missing
        tried = set()
        while True:
            rest = comp & missing
            rq = cv2.resize(rest.astype(np.uint8), (qw, qh), interpolation=cv2.INTER_NEAREST) > 0
            score = {i: int((seen[i] & rq).sum()) for i in cand if i not in tried}
            if rest.sum() < 50 or not rq.any() or not score:
                break
            i = max(score, key=lambda j: (score[j], -abs(j - ref)))
            if score[i] < max(4, 0.02 * rq.sum()):
                break
            tried.add(i)
            visible = lambda M: cv2.warpPerspective((~fg[i]).astype(np.uint8), M, (w, h), flags=cv2.INTER_NEAREST) > 0
            ring = (cv2.dilate(rest.astype(np.uint8), ring_k) > 0) & ~missing & visible(matrices[i])
            if ring.sum() < 500:
                continue
            try:
                M = _local_affine(cv2.warpPerspective(gray(frames[i]), matrices[i], (w, h)), gray(plate), ring) @ matrices[i]
            except cv2.error:
                rejected += 1
                continue
            warped, ok = cv2.warpPerspective(frames[i], M, (w, h)), visible(M)
            diff = np.abs(luma(warped) - luma(plate))
            area = rest & ok
            edge = (cv2.dilate(area.astype(np.uint8), np.ones((17, 17), np.uint8)) > 0) & ~missing & ok
            near = ring & ok
            if (near.sum() < 500 or np.median(diff[near]) > FRAME_ERR or not edge.any()
                    or (diff[edge] > 4 * FRAME_ERR).mean() > FRAME_BAD):
                rejected += 1
                continue
            plate[area] = warped[area]
            missing[area] = False
            used.add(i)
    log(f"指定第 {ref} 帧：可对齐的其他帧 {len(cand)}，跳过对齐不可靠的帧 {n - 1 - len(cand)}；"
        f"用了 {len(used)} 帧补洞，局部对不上换帧 {rejected} 次")
    return dict(plate=plate, holes=missing, valid=~missing, covered=np.ones_like(missing),
                weak=np.zeros_like(missing), ref=ref, size=(w,h), T=np.eye(3), Hs=matrices)


def save_extraction(d, image, geometry, history, label):
    name = "extract_" + uuid.uuid4().hex
    image.save(d / (name + ".png"))
    (d / (name + ".json")).write_text(json.dumps(geometry), encoding="utf-8")
    history[name] = label
    return name


def extraction_history(p, d):
    history = dict((p.data.get("panorama") or {}).get("extracts", {}))
    if not history and (d / "extract.npz").exists() and (d / "meta.json").exists():
        with np.load(d / "extract.npz") as z:
            valid = z["covered"] & ~z["holes"] & ~z["weak"]
            old = Image.fromarray(np.dstack((z["plate"], valid.astype(np.uint8)*255)))
        save_extraction(d, old, json.loads((d / "meta.json").read_text(encoding="utf-8")), history, "旧底图")
    return history


def build(p, engine, opts, log):
    """把视频整段的背景拼成一张全景大图；fill=True 时接着用 Qwen 补洞（物体身后没露出过的地方、往原片外扩）。
    opts：version（默认当前版本）、fill、grow（排除物体时外扩多少）、extend（画框外总会按现在的画幅留出透明空白）、prompt（场景描述）。"""
    vid = opts.get("version") or p.data["current"]          # 默认用当前版本，也可以指定（比如 v0 原片）
    fill = bool(opts.get("fill", True))
    grow = int(opts.get("grow", 0))

    log("读取视频…")
    frames = p.frames(vid)
    cr = p.crop_rows() if hasattr(p, "crop_rows") else None   # 裁切黑边：黑边不属于画面，提取背景时先裁掉，不拼进全景
    if cr:
        frames = frames[:, cr[0]:cr[1]]
    n, h, w = frames.shape[:3]
    fg = np.zeros((n, h, w), bool)
    objs = [o for o in p.objects_in(vid) if not o.get("prefill") and not o.get("protect")]   # 背景预填 / 保护蒙版不是物体
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1))
    selected = opts.get("objects")
    if selected is not None:
        if not isinstance(selected, list) or not set(selected) <= {o["id"] for o in objs}:
            raise ValueError("排除图层与视频版本不匹配")
        objs = [o for o in objs if o["id"] in selected]
    for o in objs:                                        # 排除所有已识别的物体（外扩一圈，盖住边缘和运动模糊）
        m = p.masks(o["id"], grow=0)
        if cr:
            m = m[:, cr[0]:cr[1]]
        for i in range(o["start"], o["end"] + 1):
            if m[i].any():
                fg[i] |= cv2.dilate(m[i].astype(np.uint8), k) > 0
    log(f"排除 {len(objs)} 个物体：{'、'.join(o['name'] for o in objs) or '无（只靠中位数去掉移动的东西）'}")
    cover = {o["name"]: float(p.masks(o["id"]).mean()) for o in objs}
    big = [k for k, v in sorted(cover.items(), key=lambda kv: -kv[1]) if v > 0.05] if fg.mean() > 0.3 else []   # 排除掉的部分太多
    try:
        mode = opts.get("mode", "panorama")
        ref = int(opts.get("ref", n // 2))
        if mode not in MODES or not 0 <= ref < n:
            raise ValueError("背景模式或指定帧无效")
        r = (frame_background(frames, fg, ref, log) if mode == "frame" else layer_background(frames, fg, ref, log)
             if mode == "layer" else panorama(frames, fg, ref=ref, log=log))
    except AlignmentError as e:
        pw, ph = e.size
        raise RuntimeError(
            f"各帧对不齐（按现在的对齐结果要拼成 {pw}x{ph}，原画面才 {w}x{h}），已停止，没有生成全景图。"
            + (f"被排除的物体一共盖住了画面的 {fg.mean() * 100:.0f}%，原因多半是「{'、'.join(big)}」：它们本身就是背景，被当成物体排除后，剩下能用来对齐的背景太少。"
               "请在图层里删掉这些物体再生成。" if big else "这个镜头可能有明显视差或剧烈运动，不适合拼全景。")
            + "另外，画面里会动的人要先识别出来，否则会被拼进背景里。") from None
    rect = frame_rect(p)
    # 所有模式都按现在的画幅：四周加“没拍到”的空白（透明），第二步 Qwen 往外画；画框比原片大多少就扩多少
    fx, fy, fw, fh = rect
    if cr:
        fy -= cr[0]                                   # 帧坐标 → 裁掉黑边后的坐标
    ext = float(opts.get("extend", 0))                # 老参数：四周按原片宽高的比例扩（可选）
    m = round(0.03 * max(w, h)) if (fx < 0 or fy < 0 or fx + fw > w or fy + fh > h) else 0   # 画框扩了：再留一点余量
    pl, pt = max(-fx, 0, round(w * ext)), max(-fy, 0, round(h * ext))
    pr_, pb = max(fx + fw - w, 0, round(w * ext)), max(fy + fh - h, 0, round(h * ext))
    pl, pt, pr_, pb = [v + m if v else 0 for v in (pl, pt, pr_, pb)]
    if pl or pt or pr_ or pb:
        pad = lambda a: np.pad(a, ((pt, pb), (pl, pr_)) + ((0, 0),) * (a.ndim - 2))
        for k in ("plate", "valid", "covered", "holes", "weak", "count"):
            if k in r:                                # 指定帧 / 背景图层模式没有 count
                r[k] = pad(r[k])
        r["T"] = np.array([[1, 0, pl], [0, 1, pt], [0, 0, 1.0]]) @ r["T"]
        r["size"] = (r["size"][0] + pl + pr_, r["size"][1] + pt + pb)
        log(f"  按画框 {fw}x{fh}（位置 {fx}, {fy}）往外扩展：左 {pl} 上 {pt} 右 {pr_} 下 {pb} 像素")
    PW, PH = r["size"]
    d = folder(p)
    history = extraction_history(p, d)
    Image.fromarray(np.dstack((r["plate"], r["valid"].astype(np.uint8)*255))).save(d / "plate.png")
    vis = r["plate"].copy()
    vis[r["weak"]] = (255, 200, 60)                       # 黄：只有几帧拍到过，不可靠，也交给 Qwen
    vis[r["holes"]] = (255, 60, 60)                       # 红：物体身后、整段都没露出过
    vis[~r["covered"]] = (60, 60, 60)                     # 深灰：原片从没拍到过的范围
    Image.fromarray(vis).save(d / "holes.png")
    stats = dict(size=[PW, PH], frame=[w, h], holes=round(float(r["holes"].sum()) / r["covered"].sum() * 100, 2),
                 weak=round(float(r["weak"].sum()) / r["covered"].sum() * 100, 2),
                 outside=round(float((~r["covered"]).mean()) * 100, 2), ref=r["ref"])
    if mode != "panorama":
        log(f"{MODES[mode]} {PW}x{PH}：仍有 {stats['holes']}% 的画面待补")
    else:
        log(f"全景 {PW}x{PH}：物体身后没露出过的 {stats['holes']}%，原片外 {stats['outside']}%")
    meta = dict(stats, mode=mode, T=r["T"].tolist(), Hs=r["Hs"].tolist(), filled=False, crop=list(cr) if cr else None,
                rect=rect)                                # 按哪个画幅提取的：画幅之后又改了，界面提示重新提取
    label = MODES[mode] + f" · 第 {ref} 帧"
    with Image.open(d / "plate.png") as plate:
        selected_extract = save_extraction(d, plate, meta, history, label)
    np.savez_compressed(d / "extract.npz", plate=r["plate"], holes=r["holes"], weak=r["weak"], covered=r["covered"])   # 中间层：Qwen 补之前
    previous = p.data.get("panorama") or {}
    kept = list(previous.get("candidates", []))
    chosen = previous.get("use", "qwen_full")
    if previous.get("filled") and chosen not in kept and (d / (chosen + ".png")).exists():
        import shutil
        name = "candidate_" + uuid.uuid4().hex
        shutil.copyfile(d / (chosen + ".png"), d / (name + ".png"))
        shutil.copyfile(d / "meta.json", d / (name + ".json"))
        kept.append(name)
        chosen = name
    if kept and chosen in kept:
        meta.update(candidates=kept, filled=True, use=chosen)
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    p.data["panorama"] = {k: v for k, v in meta.items() if k not in ("T", "Hs")} | {"v": uuid.uuid4().hex[:6], "version": vid, "extracts": history, "selected_extract": selected_extract}
    p.save()
    if fill:
        fill_holes(p, engine, opts, log)


def covered_mask(geometry, shape):
    """全景图里原片拍到过的范围（各帧画面变换进全景坐标的并集）：区分「物体身后的洞」和「画框往外扩的空白」。"""
    T, Hs = np.asarray(geometry["T"]), np.asarray(geometry["Hs"])
    fw, fh = geometry["frame"]
    ones = np.ones((fh, fw), np.uint8)
    # 指定帧 / 背景图层：底图只有参考帧的像素，原片范围 = 参考帧；全景：各帧并集
    idx = [int(geometry.get("ref", 0))] if geometry.get("mode") in ("frame", "layer") else range(0, len(Hs), max(1, len(Hs) // 40))
    cov = np.zeros(shape, bool)
    for i in idx:
        cov |= cv2.warpPerspective(ones, T @ Hs[i], shape[::-1], flags=cv2.INTER_NEAREST) > 0
    return cov


def block_holes(hole, covered, k, min_px=64):
    """物体身后的洞（原片范围里）一块一块改成方块（rect_blocks），不给 Qwen 看人形剪影（视频编辑同一个办法）。
    往原片外扩的那圈本来就是长条，不动。"""
    edge = max(3, round(0.01 * max(hole.shape)))      # 原片边上一圈不算洞：拼接 / 镜头抖动留下的细边会把所有洞连成一块
    inner = cv2.erode(covered.astype(np.uint8), np.ones((2 * edge + 1, 2 * edge + 1), np.uint8)) > 0
    n, lab = cv2.connectedComponents((hole & inner).astype(np.uint8))
    out = np.zeros_like(hole)
    near = np.ones((2 * edge + 3, 2 * edge + 3), np.uint8)
    for c in range(1, n):
        m = lab == c
        if m.sum() >= min_px:                         # 边上那一圈里属于这块洞的部分也带上（不然脚底留一条人形边）
            out |= rect_blocks(m | ((cv2.dilate(m.astype(np.uint8), near) > 0) & hole & covered & ~inner), k)
    return out & covered


def fill_holes(p, engine, opts, log, api=None):
    """第二步：读第一步提取出的背景中间层（extract.npz），用 Qwen Image 2.1 补物体身后没露出过的洞、往原片外扩。
    opts：prompt（场景描述）、seed、pe、rect（空区改成方块的段数，0 = 关）、long。
    api（云端平台，2026-11-18）：和首帧 / 尾帧的 API 模式一样，只发「底图（洞涂灰）+ 用户提示词原样」，不用 PE / seed、不缩小；
    出来的整张缩回底图大小后，仍只把洞贴回（背景图要和视频逐帧对齐，原片拍到的部分不能动）。"""
    d = folder(p)
    if not (d / "extract.npz").exists():
        raise ValueError("还没有提取背景：先点「① 从视频提取背景」")
    if api is None and not hasattr(engine, "edit_image"):
        log("当前是假引擎，跳过 Qwen 补洞")
        return
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    selected = opts.get("extract") or p.data["panorama"].get("selected_extract")
    if selected:
        if selected not in p.data["panorama"].get("extracts", {}):
            raise ValueError("所选底图不存在")
        geometry = json.loads((d / (selected + ".json")).read_text(encoding="utf-8"))
        with Image.open(d / (selected + ".png")) as image:
            rgba = np.array(image.convert("RGBA"))
    else:
        geometry = meta
        with np.load(d / "extract.npz") as z:
            valid = z["covered"] & ~z["holes"] & ~z["weak"]
            rgba = np.dstack((z["plate"], valid.astype(np.uint8) * 255))
    # 要补的地方（物体身后没露出过、拼接缝、原片外）涂灰 128 交给 Qwen。不用透明：Qwen Image 2.1 的 VAE 是 RGBA，
    # 透明的地方会原样留成紫色、根本不补（2026-11-08 4882 两次候选都是紫色人形）。首帧 / 尾帧一直是灰 128。
    hole = rgba[..., 3] == 0
    k = int(opts.get("rect") or 0)
    if k > 0:                                        # 空区改成方块：方块里原片拍到的也涂灰、整块交给 Qwen 画，贴回也整块贴（只贴人形会在方块里留下人形接缝）
        block = block_holes(hole, covered_mask(geometry, hole.shape), k)
        log(f"  留空形状（{k} 段）：洞 {hole.mean() * 100:.1f}% → {(hole | block).mean() * 100:.1f}%")
        hole |= block
    base = rgba[..., :3].copy()
    base[hole] = 128
    reference = Image.fromarray(base)
    original_size = reference.size
    long = int(opts.get("long") or BG_WORK_LONG)
    if api is not None:                              # API：原尺寸发，平台自己按比例定大小
        pass
    elif long > 0:                                     # 显式指定长边（老调用 / 排查用）
        reference.thumbnail((long, long), Image.Resampling.LANCZOS)
    else:                                            # 自动：按目标像素数换算，两边对齐到 32 的倍数
        w0, h0 = original_size
        s = min(1.0, (BG_WORK_MP * 1e6 / max(1, w0 * h0)) ** 0.5)
        if s < 1:
            tw = max(BG_WORK_MIN, round(w0 * s / BG_WORK_STEP) * BG_WORK_STEP)
            th = max(BG_WORK_MIN, round(h0 * s / BG_WORK_STEP) * BG_WORK_STEP)
            reference = reference.resize((tw, th), Image.Resampling.LANCZOS)
    qw, qh = reference.size
    log(f"  工作尺寸 {qw}×{qh}（{qw * qh / 1e6:.2f} 百万像素；原图 {original_size[0]}×{original_size[1]}）")
    reference.save(d / "qwen_input.png")
    pn = p.data["panorama"]
    name = "candidate_" + uuid.uuid4().hex
    if api is not None:
        prompt = str(opts.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("先写提示词")
        model = getattr(api, "cfg", {}).get("image_model", "")
        log(f"  API 生成（{model}），只发底图 + 提示词（原样）")
        log(f"  提示词：{prompt}")
        api.edit_image(d / "qwen_input.png", prompt, d, log=log, out_name=name + ".png")
        pn.setdefault("cand_info", {})[name] = {"time": time.strftime("%Y-%m-%d %H:%M"), "model": model, "mode": "api", "prompt": prompt}
        return _keep_candidate(p, d, pn, meta, geometry, base, hole, name, original_size, "", log)
    prompt = str(opts.get("prompt") or "").strip() or DEFAULT_PROMPT
    # 官方 PE（Qwen Image 2.1 的提示词改写模型）先看底图，把「只补背景、不要人」写成一段详细的编辑指令，再交给 Qwen。
    # 和首帧 / 尾帧走的是同一个模型（engine.rewrite_prompt），只是指令不同：背景图这边不需要人。
    pe_prompt = ""
    if opts.get("pe", True) and hasattr(engine, "rewrite_prompt"):
        log("  官方 PE 改写提示词（先看底图，把「只补背景、不要人」写成详细指令）…")
        try:
            pr = engine.rewrite_prompt([d / "qwen_input.png"], " ".join([prompt, PE_ASK]), log)
        except Exception as e:                       # PE 挂了不影响出图，退回用户写的提示词（点了停止例外：照常停）
            from engine import Cancelled
            if isinstance(e, Cancelled):
                raise
            pr = ""
            log(f"  PE 没写出来（{e}），改用原提示词")
        if pr:
            prompt, pe_prompt = pr, pr
        else:
            log("  PE 没写出来，改用原提示词")
    elif opts.get("pe", True):
        log("  当前引擎没有官方 PE，用原提示词")
    engine.edit_image(d / "qwen_input.png", prompt, d, seed=int(opts.get("seed", 42)),
                      log=log, out_name=name + ".png")
    _keep_candidate(p, d, pn, meta, geometry, base, hole, name, original_size, pe_prompt, log)


def _keep_candidate(p, d, pn, meta, geometry, base, hole, name, original_size, pe_prompt, log):
    """模型出的整张（name.png）缩回底图大小，只把洞贴回，记成背景图候选（第一张自动选用）。"""
    with Image.open(d / (name + ".png")) as output:
        generated = np.array(output.convert("RGB").resize(original_size, Image.Resampling.LANCZOS))
    Image.fromarray(generated).save(d / (name + "_qwen.png"))          # Qwen 整张原样（排查用）
    Image.fromarray(paste_holes(base, generated, hole)).save(d / (name + ".png"))
    log(f"  只把补出来的 {hole.mean() * 100:.1f}% 贴回原底图（原片拍到的部分保持原样），边缘羽化 {BG_FEATHER} 像素")
    (d / (name + ".json")).write_text(json.dumps(geometry), encoding="utf-8")
    pn.setdefault("candidates", []).append(name)
    pn.update(filled=True, v=uuid.uuid4().hex[:6], pe_prompt=pe_prompt)
    if len(pn["candidates"]) == 1:
        pn["use"] = name
    meta.update(filled=True, use=pn["use"])
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    p.save()


def remove(p):
    """删除背景全景图：项目里不再有背景图（首帧 / 尾帧、视频生成都不再用它）。文件不删，整个文件夹改名成
    panorama_已删除_<时间>，想找回可以手动改回 panorama。"""
    d = p.panorama_dir()
    if d.exists():
        d.rename(p.root / f"{d.name}_已删除_{time.strftime('%Y%m%d-%H%M%S')}")
    p.data.pop("panorama", None)
    p.save()
    return p.data


def load(project, choice=None):
    """背景全景图（面板里“后续使用”选的那张）+ 坐标变换；没生成过返回 None。"""
    pn = project.data.get("panorama") or {}
    d = project.panorama_dir()
    if choice and choice not in [*pn.get("candidates", []), "upload", "qwen_full", "filled"]:
        raise ValueError("此背景候选已失效，请重新选择")
    f = d / ((choice or pn.get("use", "qwen_full")) + ".png")
    if choice and not f.is_file():
        raise ValueError("此背景候选已失效，请重新选择")
    if pn.get("mode") and not pn.get("filled"):
        return None
    if not pn.get("filled") or not f.exists():
        f = d / "plate.png"
    if not f.exists() or not (d / "meta.json").exists():
        return None
    geometry = d / (f.stem + ".json") if f.stem in pn.get("candidates", []) else d / "meta.json"
    meta = json.loads(geometry.read_text(encoding="utf-8"))
    cur = project.crop_rows() if hasattr(project, "crop_rows") else None
    if (meta.get("crop") or None) != (list(cur) if cur else None):
        return None                                      # 画框裁切黑边改过了：旧全景图是按别的坐标拼的，请重新提取背景
    T, Hs = np.array(meta["T"]), np.array(meta["Hs"])
    fw, fh = meta.get("frame") or (project.data["w"], project.data["h"])
    Hs = smooth_homographies(T, Hs, (fw, fh), sigma=PANO_SMOOTH)   # 逐帧变换沿时间平滑：铺进画面不抖（1009 实测抖动 1.38 → 0.96，和原片一样）
    return dict(img=np.array(Image.open(f).convert("RGB")), T=T, Hs=Hs, name=f.stem, filled=f.stem != "plate")


def to_frame(p, i, d, choice=None):
    """背景全景图按第 i 帧的镜头变换回这一帧的画面 = 这一帧“人都不在时”的干净背景，存成 d/bg.png。
    没有全景图返回 None。每次出候选前重新生成：全景图可能重做过。"""
    if choice == "local":
        path = Path(d) / "bg_local.png"
        if not path.is_file():
            raise ValueError("当前图片的自定义背景已丢失")
        return path
    pano = load(p, choice)
    f = Path(d) / "bg.png"
    if pano is None:
        f.unlink(missing_ok=True)
        return None
    if not 0 <= i < len(pano["Hs"]):                    # 背景图是在帧数不同的版本里提取的（生成的版本帧数常常不一样）
        raise ValueError(f"背景图是在别的版本里提取的（只有 {len(pano['Hs'])} 帧，这里要第 {i} 帧）：在这个版本重新提取背景")
    x, y, fw, fh = frame_rect(p)                         # 按画框渲染：扩图时原片外的背景也在里面
    cr = p.crop_rows()
    if cr:
        y -= cr[0]                                       # 全景图坐标是裁掉黑边后的
    # 全景图没覆盖到的地方（画幅在提取之后又扩大了）= 空，涂灰 128；不能用边缘反射（会镜像出重复的楼 / 倒影，Qwen 照着画）
    Image.fromarray(pano_to_frame(pano["img"], pano["T"], pano["Hs"][i], (fw, fh), offset=(x, y), border=(128, 128, 128))).save(f, compress_level=1)
    return f


def _resize_to(data, w, h):
    from images import open_upload
    img = np.array(open_upload(data))
    return img if img.shape[:2] == (h, w) else cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)


def upload_plate(p, data):
    """图片编辑里上传一张要调整的背景图：代替提取出来的中间层（按中间层大小缩放，洞的位置不变），之后点 ② 补全。"""
    d = folder(p)
    if not (d / "extract.npz").exists():
        raise ValueError("先点「① 从视频提取背景」：上传的图要按提取出的全景图对齐")
    pn = p.data["panorama"]
    history = extraction_history(p, d)
    selected = pn.get("selected_extract") or next(reversed(history))
    geometry = json.loads((d / (selected + ".json")).read_text(encoding="utf-8"))
    with Image.open(d / (selected + ".png")) as plate:
        rgb = _resize_to(data, *plate.size)
        image = Image.fromarray(rgb)
        image.putalpha(plate.getchannel("A"))
    name = save_extraction(d, image, geometry, history, "上传底图")
    pn.update(extracts=history, selected_extract=name, v=uuid.uuid4().hex[:6])
    p.save()
    return p.data["panorama"]


def upload_result(p, data):
    """图片结果里上传一张调整好的背景图：按全景图大小缩放，存 upload.png，后续使用这张。"""
    d = folder(p)
    if not (d / "meta.json").exists() or not p.data.get("panorama"):
        raise ValueError("先点「① 从视频提取背景」：上传的图要按提取出的全景图对齐")
    w, h = json.loads((d / "meta.json").read_text(encoding="utf-8"))["size"]
    Image.fromarray(_resize_to(data, w, h)).save(d / "upload.png")
    p.data["panorama"].update(use="upload", filled=True, uploaded=True, v=uuid.uuid4().hex[:6])
    p.save()
    return p.data["panorama"]
