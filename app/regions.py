"""空区和重绘区：按图层的编辑算出每一帧哪些地方是空区（粗剪里涂灰 128）、哪些要重绘。

预览叠加层、生成用的 mask、参考图-首帧 / 尾帧取画面都从这里取，保证三处一致。
重绘区只有「重绘 / 不重绘」两种（生成时 ThresholdMask 0.5 本来就把中间强度二值化了）。
每一帧的结果是一张区号图：KEEP = 保留区，EMPTY = 空区（一定重绘），REDRAW = 重绘（可选蓝框），
BODY = 本体（画面里看得见的图层自己的像素，不重绘：2026-11-03 用户定，SAM 出来的个体一律不降噪，动作照粗剪），
TRANS = 过渡（重绘区往外扩的一圈，重绘但不涂灰：盖住 SAM 边缘误差、吃掉空区的硬边；代替一刀切的外扩。
        2026-11-04 用户定：照 014b 外扩 48 像素 / 2 帧，但避开本体周围一格的禁区，人所在的潜空间格不会被外扩拉进重绘）。
"""
import threading

import cv2
import numpy as np

from engine import h3_canvas, h3_frames, h3_latent_groups, h3_latent_mask

KEEP, REDRAW, EMPTY, BODY, TRANS = 0, 1, 2, 3, 4
# 过渡（H3 画布像素，项目设置 trans = {grow, avoid}，视频生成面板调）：重绘区（重绘 + 空区）往外扩 grow，
# avoid > 0：不进本体外扩 avoid 的禁区，只占保留区；avoid < 0：本体贴着重绘区的那一圈 |avoid| 也重绘。时间上前后 TRANS_T 帧（generate 里做，同样避开禁区）。
TRANS_GROW, TRANS_AVOID, TRANS_T = 48, -16, 2   # 避让 -16：人贴着重绘区那一圈也重绘（2026-11-04 用户定，passes/023 最好）
# 空区处理（图层字段，默认都是 0 / 关）：空区改成方块、人物框重绘、补被原片边截断的部分（洞外扩 API 163 删了，用户定）
MASK_OPTS_V1 = ("hole_rect", "box_redraw", "edge_fill")
# 2026-11-10 加：空区扩展（原位置的洞往外扩 / 往里收 N 原片像素）、空区时间扩展（洞取前后 ±N 帧的并集）
# 2026-11-10 再加：轮廓重绘（这个图层现在位置外面一圈 N 原片像素重绘、不涂灰，像融合带；抠图和洞都不变）
MASK_OPTS = MASK_OPTS_V1 + ("hole_dilate", "hole_t", "ring_redraw")
# 2026-11-04 用户定删掉（代码快照在 temp/snapshot_20261104_重绘简化前/）：重绘框（蓝框）+ 重绘框外扩、接触区宽度、
# 整块遮挡物、自动补被挡缺口、前后帧合并、洞圆滑。旧项目里存的这些字段不再读。
ZONE_COLORS = {REDRAW: (55, 138, 221), EMPTY: (226, 75, 74), BODY: (40, 210, 90), TRANS: (240, 160, 160)}


def redrawn(z):
    """区号图里要重绘的地方（重绘 + 空区 + 过渡；本体和保留区都不重绘）。"""
    return (z == REDRAW) | (z == EMPTY) | (z == TRANS)


def trans_opts(project):
    """项目的过渡设置（H3 画布像素）：(外扩, 主体避让)。0 外扩 = 不过渡。
    主体避让可以是负数：-16 = 本体贴着重绘区的那一圈 16 像素也重绘（人和旁边的空区能画成一体，身体中间仍钉住）。"""
    t = project.data.get("trans") or {}
    return max(0, int(t.get("grow", TRANS_GROW))), int(t.get("avoid", TRANS_AVOID))


def keepout(body, px):
    """本体外扩 px（本布局像素）= 外扩不能进的禁区。"""
    return cv2.dilate(body.astype(np.uint8), disk(px)) > 0 if px > 0 else body


def ext_opts(box_ext, s=1.0):
    """图层的扩大重绘范围 box_ext → 每边拉出去多少（本布局像素）、沿边的范围（外框高 / 宽的比例 0~1）、是否延续。
    box_ext = {t, b, l, r: 原画面像素, t0 / t1、b0 / b1（左右范围）、l0 / l1、r0 / r1（上下范围）, cont: 默认 True}。"""
    e = box_ext or {}
    out = {k: int(round(float(e.get(k) or 0) * s)) for k in "tblr"}
    out["span"] = {k: (min(max(float(e.get(k + "0", 0)), 0), 1), min(max(float(e.get(k + "1", 1)), 0), 1)) for k in "tblr"}
    out["cont"] = e.get("cont", True) is not False
    return out


def ext_masks(o):
    """图层的跟随蒙版（2026-11-08 用户定，代替单个 box_ext）：o["ext_masks"] = [{id, name, t, b, l, r, t0 … r1, cont,
    start, end, off}]，每个 = 一份扩大重绘范围 + 自己的帧范围（start / end 是视频帧号，没有 = 所有帧），off = 停用。
    旧项目只有 box_ext：当成一个所有帧都生效的跟随蒙版（不改盘上的数据）。返回启用的、拉开了边的那些。"""
    if isinstance(o.get("ext_masks"), list):
        lst = o["ext_masks"]
    else:
        e = o.get("box_ext") or {}
        lst = [dict(e, id="e1", name="主体扩绘区 1")] if any(e.get(k) for k in "tblr") else []
    return [m for m in lst if not m.get("off") and any(m.get(k) for k in "tblr")]


def keep_masks(o):
    """主体局部保留（2026-11-11 用户定，图层字段 keep_masks）：只对这个主体起作用的保护范围。
    o["keep_masks"] = [{id, name, frame, start, end, off}]，形状存在 masks/keep_<id>.png：画的时候按这个主体在第 frame 帧
    原片里的外接框裁下来的（原片坐标，画在移动后的位置上的会先按图层变换倒回去）。每一帧按这一帧主体的外接框缩放摆上去
    （跟着主体走）。帧范围里：主体在范围里的像素照常贴，范围外的像素不贴、算空区（涂灰、完全重绘）。返回启用的那些。"""
    return [m for m in (o.get("keep_masks") or []) if not m.get("off")]


def load_keep(project, kid):
    """主体局部保留的形状（外接框里的 bool 图）；文件不在了返回 None（当它不存在）。"""
    f = project.root / "masks" / f"keep_{kid}.png"
    if not f.is_file():
        return None
    m = cv2.imdecode(np.fromfile(str(f), np.uint8), cv2.IMREAD_GRAYSCALE)
    return None if m is None or not (m > 127).any() else m > 127


def keep_region(src, crops, h, w):
    """把主体局部保留摆到这一帧：src = 这一帧主体的遮罩（任意分辨率，取外接框），crops = 形状列表，输出 h × w 的保护范围。"""
    ys, xs = np.nonzero(src)
    out = np.zeros((h, w), bool)
    if not len(xs):
        return out
    ky, kx = h / src.shape[0], w / src.shape[1]
    x0, y0 = int(round(xs.min() * kx)), int(round(ys.min() * ky))
    x1, y1 = max(x0 + 1, int(round((xs.max() + 1) * kx))), max(y0 + 1, int(round((ys.max() + 1) * ky)))
    for c in crops:
        r = cv2.resize(c.astype(np.uint8), (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST) > 0
        out[y0:y1, x0:x1] |= r[:h - y0, :w - x0]
    return out


def ext_band(m, e):
    """一帧的扩大重绘范围（2026-11-04 用户定）：
    - 拉开的那条边往外一条带，只在这条边的范围里（左右边按外框高度的比例 r0~r1，上下边按宽度的比例），跟着物体外框每帧变；
    - 延续（默认开）：同一范围里，从物体轮廓到外框那条边之间的空隙也算进来，带从物体边上连续接出去；
    - 相邻两边都拉开时，两条带之间的角也补上（2026-11-14 用户定：不管两边的范围到不到这个角）。"""
    h, w = m.shape
    ys, xs = np.nonzero(m)
    x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    bw, bh = x1 - x0, y1 - y0
    sp = e["span"]
    rng = lambda k, lo, n: (lo + int(round(sp[k][0] * n)), lo + int(round(sp[k][1] * n)))
    band = np.zeros((h, w), bool)
    sub = m[y0:y1, x0:x1]
    rows, cols = sub.any(1), sub.any(0)
    for k in "tblr":
        if not e[k]:
            continue
        if k in "lr":
            a, b = rng(k, y0, bh)
            xa, xb = (x1, x1 + e["r"]) if k == "r" else (x0 - e["l"], x0)
            band[max(a, 0):min(b, h), max(xa, 0):min(xb, w)] = True
        else:
            a, b = rng(k, x0, bw)
            ya, yb = (y1, y1 + e["b"]) if k == "b" else (y0 - e["t"], y0)
            band[max(ya, 0):min(yb, h), max(a, 0):min(b, w)] = True
        if e["cont"]:                                    # 外框里：物体轮廓到这条边之间
            inner = np.zeros_like(sub)
            if k == "r":
                inner = np.arange(bw)[None, :] > np.where(rows, bw - 1 - np.argmax(sub[:, ::-1], 1), -1)[:, None]
            elif k == "l":
                inner = np.arange(bw)[None, :] < np.where(rows, np.argmax(sub, 1), bw)[:, None]
            elif k == "b":
                inner = np.arange(bh)[:, None] > np.where(cols, bh - 1 - np.argmax(sub[::-1], 0), -1)[None, :]
            else:
                inner = np.arange(bh)[:, None] < np.where(cols, np.argmax(sub, 0), bh)[None, :]
            lim = np.zeros_like(sub)
            a, b = rng(k, 0, bh if k in "lr" else bw)
            if k in "lr":
                lim[max(a, 0):b] = True
            else:
                lim[:, max(a, 0):b] = True
            band[y0:y1, x0:x1] |= inner & lim & ~sub
    for v, hz in (("t", "l"), ("t", "r"), ("b", "l"), ("b", "r")):   # 角：相邻两边都拉开就补
        if e[v] and e[hz]:
            ya, yb = (y0 - e["t"], y0) if v == "t" else (y1, y1 + e["b"])
            xa, xb = (x0 - e["l"], x0) if hz == "l" else (x1, x1 + e["r"])
            band[max(ya, 0):min(yb, h), max(xa, 0):min(xb, w)] = True
    return band


def _runs(v, gap=0):
    """一维布尔数组里连续为 True 的段 [(起, 止)]；中间断开不超过 gap 的算同一段。"""
    idx = np.flatnonzero(v)
    if not len(idx):
        return []
    cut = np.flatnonzero(np.diff(idx) > gap + 1)
    starts, ends = np.r_[idx[0], idx[cut + 1]], np.r_[idx[cut], idx[-1]] + 1
    return list(zip(starts.tolist(), ends.tolist()))


def disk(px):
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1))


def obj_height(m):
    """物体在画面里的典型身高（像素）：每帧遮罩上下范围的中位数。"""
    rows = m.any(2)
    hs = [int(np.ptp(np.nonzero(r)[0])) + 1 for r in rows if r.any()]
    return float(np.median(hs)) if hs else 0.0


def temporal_grow(masks, r):
    """时间上外扩：每帧取前后 r 帧的并集，盖住快速运动时拖出遮罩的运动模糊残影。"""
    return np.stack([masks[max(k - r, 0):k + r + 1].any(0) for k in range(len(masks))]) if r else masks


def is_identity(tf):
    tf = tf or {}
    return not tf.get("x") and not tf.get("y") and tf.get("sx", 1) == 1 and tf.get("sy", 1) == 1 and not tf.get("rot")


def layer_matrix(o):
    """图层变换（视频像素）：p' = c + t + R·S·(p - c)，c = 物体整体外框中心（和前端 apply() 一致）。返回 2x3。"""
    tf = o.get("tf") or {}
    x0, y0, x1, y1 = o["bbox"]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    r = np.deg2rad(tf.get("rot", 0))
    sx, sy = tf.get("sx", 1), tf.get("sy", 1)
    A = np.array([[np.cos(r) * sx, -np.sin(r) * sy], [np.sin(r) * sx, np.cos(r) * sy]])
    t = np.array([cx + tf.get("x", 0), cy + tf.get("y", 0)]) - A @ np.array([cx, cy])
    return np.hstack([A, t[:, None]]).astype(np.float32)


def _warp(m, M, w, h):
    return cv2.warpAffine(m.astype(np.uint8), M, (w, h), flags=cv2.INTER_NEAREST) > 0


def cell_zones(zones):
    """H3 实际收到的分区：按 H3 画布缩放、补帧，每个潜空间格取格里最大的区号（有一点重绘整格就重绘），
    再放大回原画面每一帧。返回 (n,h,w) uint8，值同 zones。优先级：保留 < 本体 < 过渡 < 重绘 < 空区。"""
    n, h, w = zones.shape
    W, H = h3_canvas(w, h)
    F = h3_frames(n)
    lat = None
    for k in (BODY, TRANS, REDRAW, EMPTY):
        m = np.stack([cv2.resize((z == k).astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST) > 0 for z in zones])
        if F > n:
            m = np.concatenate([m, np.repeat(m[-1:], F - n, 0)])
        lm = h3_latent_mask(m)
        lat = np.zeros(lm.shape, np.uint8) if lat is None else lat
        lat[lm] = k
    out = np.zeros_like(zones)
    for t, (a, b) in enumerate(h3_latent_groups(F)):
        if a < n:
            out[a:min(b, n)] = cv2.resize(lat[t], (w, h), interpolation=cv2.INTER_NEAREST)
    return out


def zone_overlay(img, z):
    v = img.copy()
    for k, c in ZONE_COLORS.items():
        m = z == k
        v[m] = (v[m] * 0.45 + np.array(c, np.float32) * 0.55).astype(np.uint8)
    return v


class _Layout:
    """合成用到的图层布局：哪些动过、变换矩阵、按身高算的像素参数。整段合成、单帧（首帧 / 尾帧标签）和
    界面上的“重绘范围”叠加层共用。scale < 1：在缩小的分辨率上算（叠加层用，快几倍），不读视频帧。"""

    def __init__(self, project, op, allow_unchanged=False, scale=1.0):
        self.project = project
        vids = {v["id"] for v in project.data.get("versions", [])}
        self.base = (op or {}).get("version") if (op or {}).get("version") in vids else project.data["current"]   # 排队的生成：按点击时的版本
        self.n, H, W = project.data["n"], project.data["h"], project.data["w"]
        self.s = float(scale)
        self.h, self.w = (H, W) if self.s == 1 else (round(H * self.s), round(W * self.s))
        objs = sorted(project.objects_in(self.base), key=lambda o: o.get("z") or 0)     # 从后往前
        if hasattr(project, "layer_order"):
            objs = project.layer_order(self.base)
        self.prefill = [o for o in objs if o.get("prefill") and not o.get("off")]   # 背景预填图层：不算空区 / 本体，只在生成时换空区的填充（generate.prefill）
        self.protect = [o for o in objs if o.get("protect") and not o.get("off")]   # 保护蒙版：画的地方最后一律算本体（不重绘）
        objs = [o for o in objs if not o.get("prefill") and not o.get("protect") and not (o.get("shape") and o.get("off"))]   # 停用的固定蒙版：当它不存在
        self.objs = objs
        matrix_of = getattr(project, "layer_matrix", layer_matrix)
        visible = getattr(project, "layer_visible", lambda o: o.get("visible", True))
        matrices = {o["id"]: matrix_of(o) for o in objs}
        moved = lambda o: not np.allclose(matrices[o["id"]], np.eye(3)[:2])
        self.lifted = [o for o in objs if not visible(o) or moved(o)]
        framed = frame_rect(project) != [0, 0, W, H]       # 只扩了画框（图层没动）也要能合成：画框外完全重绘
        self.shown = [o for o in objs if visible(o)]
        # 主体身上要留空的部分：轮廓重绘为负数（贴边一圈）、主体局部保留（范围外）。没移动的主体也生效
        self.shrink = {o["id"]: -float(o["ring_redraw"]) for o in self.shown if float(o.get("ring_redraw") or 0) <= -1}
        self.keep = {}
        for o in self.shown:
            lst = [(c, m.get("start"), m.get("end")) for m in keep_masks(o) for c in [load_keep(project, m["id"])] if c is not None]
            if lst:
                self.keep[o["id"]] = lst
        self.trim = set(self.shrink) | set(self.keep)
        if not self.lifted and not framed and not self.trim and not allow_unchanged:
            raise ValueError("图层都没动过：先在画面上移动 / 缩放 / 旋转，或隐藏物体，或改画框")
        self.moved = [o for o in self.shown if moved(o)]
        self.full = {o["id"]: project.masks(o["id"]) for o in objs}
        self.full.update({o["id"]: project.masks(o["id"]) for o in self.protect})   # 保护蒙版（不在 objs 里）也要能按帧取
        # 2026-11-10 用户定「按 PS 的做法，各管各的」：抠图的收缩 / 扩展只管图层带走哪些像素——扩出来的那一圈跟着人
        # 一起贴到新位置（不再自动变成空区）；原位置的洞跟抠图一样大。要在人外面加一圈重绘，用蒙版的「轮廓重绘」（ring_redraw，涂灰的空区）。
        # （2026-10-27 的旧规则「新位置外扩那一圈是空的」已去掉；core 保留成和 full 同一份，cmask 等调用照旧。）
        self.core = {o["id"]: self.full[o["id"]] for o in objs}
        # 轮廓重绘为负数（ring_redraw < 0）：图层身上贴边的那一圈不跟着贴，留空（涂灰、完全重绘）；原位置的洞不变（self.shrink 在上面）
        S = np.diag([self.s, self.s, 1.0])
        self.Ms = {o["id"]: (S @ np.vstack([matrices[o["id"]], [0, 0, 1]]) @ np.linalg.inv(S))[:2].astype(np.float32)
                   for o in self.shown}
        # 2026-10-31 用户定：不许任何自动外扩。没在图层面板里手动设的一律 0；这些处理都是蒙版工具里的选项（MASK_OPTS），默认关。
        # 重绘范围往外扩展（上下左右，原画面像素）：用户在画面上拖扩展框的四条边。每一帧加在物体（新位置）这一帧的外框四边，
        # 跟着物体走、跟着外框变宽变窄。比如人缩小了、原片只拍到半身，往下拉 = 让 H3 把下半身画出来。
        # 跟随蒙版：每个图层可以有好几个，各自有帧范围（ext_masks）
        self.ext = {o["id"]: [(ext_opts(m, self.s), m.get("start"), m.get("end")) for m in ext_masks(o)] for o in self.shown if ext_masks(o)}
        fx, fy, fw, fh = frame_rect(project)
        self.trans_grow, self.trans_avoid = trans_opts(project)
        self.trans_k = fw / h3_canvas(fw, fh)[0] * self.s     # 1 个 H3 画布像素 = 多少个本布局像素（H3 按输出画幅定画布）
        self._cache = {}
        self._frames = None
        self._one = {}

    @property
    def frames(self):
        if self._frames is None:
            self._frames = self.project.frames(self.base)
        return self._frames

    def frame(self, i):
        """第 i 帧：整段已经读进来就直接取，否则只解码这一帧（单帧合成用，快很多）。"""
        if self._frames is not None:
            return self._frames[i]
        if i not in self._one:
            self._one[i] = self.project.frame(self.base, i)
        return self._one[i]

    @property
    def masks(self):                                     # 全分辨率（贴图层用）
        return self.full

    def mask(self, oid, i):
        if self.s == 1:
            return self.full[oid][i]
        k = (oid, i)
        if k not in self._cache:
            self._cache[k] = cv2.resize(self.full[oid][i].astype(np.uint8), (self.w, self.h), interpolation=cv2.INTER_NEAREST) > 0
        return self._cache[k]

    def _erode(self, m, px):
        r = int(round(px))
        if r < 1 or not m.any():
            return m
        return cv2.erode(m.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))) > 0

    def keep_i(self, oid, i, h, w):
        """第 i 帧这个主体的保护范围（h × w，原片坐标）；这一帧没有生效的主体局部保留 = None（整个主体照贴）。"""
        crops = [c for c, a, b in self.keep.get(oid, ()) if (a is None or i >= a) and (b is None or i <= b)]
        if not crops:
            return None
        return keep_region(self.full[oid][i], crops, h, w)

    def core_i(self, oid, i):
        """第 i 帧贴到画面上的抠图（全分辨率）：轮廓重绘为负数时往里收；有主体局部保留时只留保护范围里的。去掉的部分留空。"""
        m = self.core[oid][i]
        if oid in self.shrink:
            m = self._erode(m, self.shrink[oid])
        kp = self.keep_i(oid, i, *m.shape) if oid in self.keep else None
        return m & kp if kp is not None else m

    def cmask(self, oid, i):
        """贴到画面上的抠图，按这个布局的分辨率（轮廓重绘为负数时往里收；主体局部保留范围外不贴）。"""
        if oid not in self.trim:
            return self.mask(oid, i)
        k = ("core", oid, i)
        if k not in self._cache:
            m = self.mask(oid, i)
            if oid in self.shrink:
                m = self._erode(m, self.shrink[oid] * self.s)
            kp = self.keep_i(oid, i, self.h, self.w) if oid in self.keep else None
            self._cache[k] = m & kp if kp is not None else m
        return self._cache[k]

    def all_i(self, i):
        m = np.zeros((self.h, self.w), bool)
        for o in self.objs:
            m |= self.mask(o["id"], i)
        return m

    def alpha(self, oid, i):
        """MatAnyone 的半透明遮罩（0~255，按这个布局的分辨率）；没做过 = None。"""
        a = self.project.proxy_alpha(oid, i)
        if a is None:
            return None
        return a if a.shape == (self.h, self.w) else cv2.resize(a, (self.w, self.h), interpolation=cv2.INTER_LINEAR)

    def zones_i(self, i):
        """第 i 帧的区号图（KEEP / EMPTY / BODY / TRANS），以及粗剪里涂灰 128 的地方（= 空区里不是本体的部分）。"""
        h, w = self.h, self.w
        pl, covered, own = self._place(i)
        src, bands = self._sources(i, pl, covered, own)
        full, body = np.zeros((h, w), bool), np.zeros((h, w), bool)
        for m in src.values():
            full |= m
        for o in self.shown:                             # 本体 = 看得见的图层自己的像素（移动过的扣掉边缘外扩那一圈）
            body |= own[o["id"]] & ~bands[o["id"]] if o["id"] in bands else own[o["id"]]
        z = np.zeros((h, w), np.uint8)
        z[body] = BODY                                   # 本体不重绘
        z[full] = EMPTY                                  # 空区压过本体（边缘外扩那一圈）
        self._trans(z)
        if (self.project.data.get("trans") or {}).get("blue"):
            box = self.blue_box(i)
            z[(box > 0) & (z == KEEP)] = REDRAW
        cr = self.project.crop_rows() if hasattr(self.project, "crop_rows") else None
        if cr:                                           # 裁切黑边：黑边不属于画面。画框扩到原片外时，黑边那几行也是空的，要重绘
            s = self.h / float(self.project.data["h"])
            a, b = int(round(cr[0] * s)), int(round(cr[1] * s))
            bars = np.zeros_like(z, bool)
            if a > 0:
                bars[:a] = True
            if b < self.h:
                bars[b:] = True
            z[bars] = EMPTY
            full |= bars
        if self.protect:                                 # 保护蒙版：压过一切，当本体（不涂灰、不重绘、时间外扩也避开）
            pm = np.zeros((h, w), bool)
            for o in self.protect:
                pm |= self.mask(o["id"], i)
            z[pm] = BODY
            full &= ~pm
            body |= pm
        return z, full & ~body

    def blue_box(self, i):
        box = np.zeros((self.h, self.w), np.uint8)
        for o in self.lifted:
            ys, xs = np.nonzero(self.mask(o["id"], i))
            if not len(xs):
                continue
            pad = max(0, round((o.get("box_margin") or 0) * self.s))
            left, right = xs.min() - pad, xs.max() + pad
            top, bottom = ys.min() - pad, ys.max() + pad
            box[max(0,top):bottom+1, max(0,left):right+1] = 1
            if o in self.moved:
                corners = np.array([[left,top],[right,top],[right,bottom],[left,bottom]])
                matrix = self.Ms[o["id"]]
                cv2.fillConvexPoly(box, np.rint(corners @ matrix[:,:2].T + matrix[:,2]).astype(np.int32), 1)
        return box

    def holes_by_layer(self, i):
        """第 i 帧每个图层造成的空区 {图层 id: 遮罩}（这个布局的分辨率、原片坐标），和 zones_i 同一套规则：
        原位置的洞（+ 方块）、移动后边缘外扩那一圈、扩大重绘范围的带。补瑕疵提示词按图层分块描述用。"""
        pl, covered, own = self._place(i)
        return self._sources(i, pl, covered, own)[0]

    def _place(self, i):
        """第 i 帧每个看得见的图层现在的位置 pl、所有图层盖住的地方 covered、每个图层自己（没被前面的图层盖住）的部分 own。"""
        h, w, Ms = self.h, self.w, self.Ms
        pl = {o["id"]: (_warp(self.mask(o["id"], i), Ms[o["id"]], w, h) if o in self.moved else self.mask(o["id"], i))
              for o in self.shown}
        covered = np.zeros((h, w), bool)
        own = {}
        for o in reversed(self.shown):                   # 从前往后：被前面的图层盖住的部分不算自己的
            own[o["id"]] = pl[o["id"]] & ~covered
            covered |= pl[o["id"]]
        return pl, covered, own

    def _sources(self, i, pl, covered, own):
        """第 i 帧每个图层造成的空区 {图层 id: 遮罩}（zones_i 取并集，holes_by_layer 原样给补瑕疵分块），
        以及移动过的图层新位置边缘外扩那一圈 {图层 id: 遮罩}（zones_i 从本体里扣掉）。"""
        h, w, Ms = self.h, self.w, self.Ms
        out, bands = {}, {}

        def add(oid, m):
            out[oid] = out[oid] | m if oid in out else m
        for o in self.lifted:                            # 原位置：空出来的洞 = 空区（粗剪里涂灰，完全重绘；不铺全景图、不模糊填充）
            b = self._hole_src(o, i)
            if b.any():                                  # 空区改成方块（不留人形：H3 / Qwen 会照人形空洞画出人）
                # 往里收（空区扩展为负）最多收到这一帧物体自己的轮廓：原来的人不能从洞里露出来（和预览一样）
                add(o["id"], (self._hole_grow(o, self._hole_shape(o, b)) | self.mask(o["id"], i)) & ~covered)
        for o in self.shown:                             # 轮廓重绘为负数 / 主体局部保留范围外：图层身上不贴的部分 = 空区（完全重绘）
            if o["id"] in self.trim:
                c = self.cmask(o["id"], i)
                bands[o["id"]] = own[o["id"]] & ~(_warp(c, Ms[o["id"]], w, h) if o in self.moved else c)
                add(o["id"], bands[o["id"]])
        for o in self.moved:
            if o.get("edge_fill"):                       # 补被原片边截断的部分
                ef = self._edge_fill(o, i)
                if ef is not None:
                    add(o["id"], ef & ~covered)
        for oid, lst in self.ext.items():                # 跟随蒙版（扩大重绘范围）：外框四边拉出去的带 = 空区（交给 H3 / Qwen 画），跟着物体走
            for e, a, b in lst:
                if (a is None or i >= a) and (b is None or i <= b) and pl[oid].any():
                    add(oid, ext_band(pl[oid], e) & ~covered)
        for oid, m in self._box_redraw(pl, covered).items():   # 人物框重绘：框里除了所有图层的本体都是空区
            add(oid, m)
        for o in self.shown:                             # 轮廓重绘（正数）：图层现在位置外面一圈 = 空区（涂灰、完全重绘），不压别的图层本体
            r = int(round(float(o.get("ring_redraw") or 0) * self.s))
            if r >= 1 and pl[o["id"]].any():
                k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
                add(o["id"], (cv2.dilate(pl[o["id"]].astype(np.uint8), k) > 0) & ~covered)
        return out, bands

    def _edge_fill(self, o, i):
        """补被原片边截断的部分（图层字段 edge_fill，默认关）：这一帧图层贴着原片边缘（左右 = 原片左右边，上下 = 原片上下边，
        去了黑边就是黑边内侧那一行）的那几段，移动后那条边跑进了画面里，人在那条边被直接截断。
        从贴边的那一段沿原片边往外推出去（一直推出画面），按图层变换摆到新位置 = 空区，让 H3 把截掉的部分画出来。
        只在贴边的帧有；没动过的图层推出去的部分在原片外，不影响。纯几何，不分物体种类。"""
        m = self.cmask(o["id"], i)
        if not m.any():
            return None
        h, w = m.shape
        top, bot = 0, h
        cr = self.project.crop_rows() if hasattr(self.project, "crop_rows") else None
        if cr:
            k = h / float(self.project.data["h"])
            top, bot = int(round(cr[0] * k)), int(round(cr[1] * k))
        tol = max(1, int(round(2 * self.s)))             # SAM 的边不一定正好压在最后一个像素上
        far = 4 * (h + w)                                # 推出去多远：保证出画面
        M = self.Ms[o["id"]]
        out = np.zeros((h, w), np.uint8)
        # 每条边：贴边的那一维、沿边的起点偏移、原片边的位置、往外的方向
        edges = (("l", m[top:bot, :tol].any(1), top, 0, (-1, 0)), ("r", m[top:bot, w - tol:].any(1), top, w, (1, 0)),
                 ("t", m[top:top + tol].any(0), 0, top, (0, -1)), ("b", m[bot - tol:bot].any(0), 0, bot, (0, 1)))
        for k, touch, off, pos, (nx, ny) in edges:
            for a, b in _runs(touch, gap=3 * tol):
                a, b = a + off, b + off - 1                # 像素下标（含两头）
                seg = [(pos, a), (pos, b)] if k in "lr" else [(a, pos), (b, pos)]
                poly = np.array(seg + [(seg[1][0] + far * nx, seg[1][1] + far * ny), (seg[0][0] + far * nx, seg[0][1] + far * ny)],
                                np.float64)
                cv2.fillConvexPoly(out, np.rint(poly @ M[:, :2].T + M[:, 2]).astype(np.int32), 1)
        return out > 0

    def _box_redraw(self, pl, covered):
        """人物框重绘（图层勾了 box_redraw）：这一帧图层（连同它看得见的从属图层）在现在位置的外接框里，
        不属于任何看得见的图层的部分 {图层 id: 遮罩}。别的图层的本体照常保留。"""
        out = {}
        for o in self.shown:
            if not o.get("box_redraw"):
                continue
            m = pl[o["id"]].copy()
            for c in self.shown:
                if c.get("parent_id") == o["id"]:
                    m |= pl[c["id"]]
            ys, xs = np.nonzero(m.any(1))[0], np.nonzero(m.any(0))[0]
            if not len(ys):
                continue
            box = np.zeros_like(m)
            box[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1] = True
            out[o["id"]] = box & ~covered
        return out

    def _hole_src(self, o, i):
        """原位置的洞（还没改形状）：空区时间扩展 hole_t = N 时取第 i−N…i+N 帧的并集（快动作时重绘范围不跟着每帧乱跳）。"""
        t = int(o.get("hole_t") or 0)
        b = self.mask(o["id"], i)
        if t <= 0:
            return b
        b = b.copy()
        for j in range(max(0, i - t), min(self.n - 1, i + t) + 1):
            if j != i:
                b |= self.mask(o["id"], j)
        return b

    def _hole_grow(self, o, m):
        """空区扩展 hole_dilate（原片像素，负数往里收）：只改这个图层原位置留下的洞，人的抠图不变。"""
        d = int(o.get("hole_dilate") or 0)
        r = int(round(abs(d) * self.s))
        if not d or r < 1 or not m.any():
            return m
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        return (cv2.dilate if d > 0 else cv2.erode)(m.astype(np.uint8), k) > 0

    def _hole_shape(self, o, b):
        """原位置的洞：空区改成方块（横切 k 段，每段换成外接长方形）。"""
        return rect_blocks(b, int(o.get("hole_rect") or 0))

    def _trans(self, z):
        """过渡：重绘区往外扩 trans_grow，避开本体外扩 trans_avoid 的禁区，只改 KEEP 的像素；
        trans_avoid < 0 时反过来：本体贴着重绘区的那一圈 |trans_avoid| 也改成过渡（重绘）。"""
        d = int(round(self.trans_grow * self.trans_k))
        if d <= 0:
            return
        red = ((z == REDRAW) | (z == EMPTY)).astype(np.uint8)
        av = int(round(self.trans_avoid * self.trans_k))
        g = cv2.dilate(red, disk(d)) > 0
        z[g & (z == KEEP) & ~keepout(z == BODY, max(av, 0))] = TRANS
        if av < 0:
            z[(cv2.dilate(red, disk(-av)) > 0) & (z == BODY)] = TRANS

    def paint_i(self, comp_i, i, empty):
        """就地（全分辨率）：空区涂灰 128（空就是空），再把图层从后往前贴回（移动过的按变换贴到新位置）。"""
        comp_i[empty] = 128
        h, w = self.h, self.w
        for o in self.shown:
            src = self.core_i(o["id"], i)
            if not src.any():
                continue
            a = cv2.GaussianBlur(src.astype(np.float32), (5, 5), 0)
            rgb = self.frame(i)
            if o in self.moved:
                a = cv2.warpAffine(a, self.Ms[o["id"]], (w, h))
                rgb = cv2.warpAffine(self.frame(i), self.Ms[o["id"]], (w, h))
            a = a[..., None]
            comp_i[:] = (comp_i * (1 - a) + rgb * a).clip(0, 255).astype(np.uint8)
        return comp_i


    def paint_framed(self, canvas, i, rect):
        """就地：在画框坐标（rect = 原片坐标里的 x, y, w, h）里把图层从后往前贴上。扩图时移出原片的那部分人
        也贴得上（在原片大小里合成会被截掉）。返回 (所有图层盖住的地方, 移动过的图层盖住的地方)。"""
        x, y, fw, fh = rect
        Tm = np.array([[1, 0, -x], [0, 1, -y], [0, 0, 1]], np.float64)
        cov = np.zeros((fh, fw), bool)
        cov_moved = np.zeros((fh, fw), bool)
        for o in self.shown:
            src = self.core_i(o["id"], i)
            if not src.any():
                continue
            M = np.eye(3)
            if o in self.moved:
                M[:2] = self.project.layer_matrix(o) if hasattr(self.project, "layer_matrix") else layer_matrix(o)
            A = (Tm @ M)[:2].astype(np.float32)
            a = cv2.warpAffine(cv2.GaussianBlur(src.astype(np.float32), (5, 5), 0), A, (fw, fh))
            rgb = cv2.warpAffine(self.frame(i), A, (fw, fh))
            canvas[:] = (canvas * (1 - a[..., None]) + rgb * a[..., None]).clip(0, 255).astype(np.uint8)
            cov |= a > 0.5
            if o in self.moved:
                cov_moved |= a > 0.5
        return cov, cov_moved


_overlay_layouts = {}
_cell_overlays = {}


def explicit_mask(zones, canvas_size, radius, layout=None):
    """Keep existing empty/transition pixels; additions yield to body anywhere in the H3 group."""
    W, H = canvas_size
    def resize(mask):
        return cv2.resize(mask.astype(np.uint8), (W,H), interpolation=cv2.INTER_NEAREST).astype(bool)
    base = np.stack([resize((z == EMPTY) | (z == TRANS)) for z in zones])
    use_boxes = layout is not None and (layout.project.data.get("trans") or {}).get("blue")
    if use_boxes:
        rect = [round(v * layout.s) for v in frame_rect(layout.project)]
    groups = h3_latent_groups(len(zones))
    protected = []
    for a,b in groups:
        body = np.logical_or.reduce([resize(z == BODY) for z in zones[a:b]])
        occupied = body.reshape(H//32,32,W//32,32).any((1,3))
        allow = ~occupied.repeat(32,0).repeat(32,1)
        protected.append(allow)
        if use_boxes:
            blue = np.logical_or.reduce([resize(to_frame(layout.blue_box(i), rect, 0)) for i in range(a,b)])
            box = blue.reshape(H//32,32,W//32,32).any((1,3))
            base[a:b] |= box.repeat(32,0).repeat(32,1) & allow
    if radius:
        merged = temporal_grow(base, radius)
        for (a,b),allow in zip(groups,protected):
            base[a:b] |= merged[a:b] & allow
    return base


def overlay_cells(zones, canvas_size, trans, source_width, settings=None, layout=None):
    """仅供预览：生成的时间避让 → H3 尺寸 → 补帧 → token/时间组 max。

    返回紧凑潜格，不保存整段 H3 大图。1 红、3 绿、4 黄（本体与重绘共格）。
    zones 来自预览布局，故轮廓边缘仍可能有半格误差。
    """
    n, h, w = zones.shape
    W, H = canvas_size
    def spatial(m):
        full = cv2.resize(m.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST) > 0
        return h3_latent_mask(full[None])[0]
    if settings and ("frames" in settings or settings.get("blue")):
        mask = explicit_mask(zones, canvas_size, settings.get("frames", 0), layout)
    else:
        mask = redrawn(zones)
        grow, avoid = trans
        if grow > 0:
            av = int(round(avoid * source_width / W))
            av = int(round(av * w / source_width))
            mask |= temporal_grow(mask, TRANS_T) & ~np.stack([keepout(z == BODY, av) for z in zones])
    reds = np.stack([spatial(m) for m in mask])
    bodies = np.stack([spatial(z == BODY) for z in zones])
    F = h3_frames(n)
    if F > n:
        reds = np.concatenate([reds, np.repeat(reds[-1:], F - n, 0)])
        bodies = np.concatenate([bodies, np.repeat(bodies[-1:], F - n, 0)])
    out = []
    for a, b in h3_latent_groups(F):
        r = reds[a:b].any(0)
        for i in range(a, min(b, n)):
            body = bodies[i]
            z = np.zeros(r.shape, np.uint8)
            z[body] = 3
            z[r] = 1
            z[r & body] = 4
            out.append(z)
    return out


def cell_overlay_map(project, i, sig, lock):
    """后台缓存整段，等待时返回 None；不阻塞播放或普通像素分区。"""
    key = (str(project.root), sig, project.pw())
    state = _cell_overlays.get(key)
    if state is None:
        _cell_overlays.clear()
        state = _cell_overlays[key] = {}
        def warm():
            try:
                maps = []
                for k in range(project.data['n']):
                    with lock:
                        if _cell_overlays.get(key) is not state:
                            return
                        maps.append(zone_overlay_map(project, k, sig, lock))
                zones = np.stack(maps)
                _, _, fw, fh = frame_rect(project)
                cells = overlay_cells(zones, h3_canvas(fw, fh), trans_opts(project), fw, project.data.get("trans"), _overlay_layouts[key])
                state['size'] = (zones.shape[2], zones.shape[1])
                state['cells'] = cells
            except Exception as exc:
                state['error'] = str(exc)
        threading.Thread(target=warm, daemon=True).start()
    if 'error' in state:
        raise RuntimeError(state['error'])
    if 'cells' not in state:
        return None
    return cv2.resize(state['cells'][i], state['size'], interpolation=cv2.INTER_NEAREST)


def zone_overlay_map(project, i, sig, lock=None):
    """界面“重绘范围”叠加层：第 i 帧的分区，预览分辨率（宽 960）。图层状态一变，后台把整段都先算好
    （一帧约 0.1 秒），播放、拖时间轴时直接取。"""
    import threading
    key = (str(project.root), sig, project.pw())          # 预览宽度改了：分区按新的预览大小重算
    L = _overlay_layouts.get(key)
    if L is None:
        _overlay_layouts.clear()                         # 只留当前这一份：图层一改就作废
        L = _overlay_layouts[key] = _Layout(project, {}, allow_unchanged=True, scale=project.pw() / project.data["w"])
        L.zmaps = {}

        def warm():                                      # 从当前帧往后、再往前，一帧帧算好
            order = list(range(i, L.n)) + list(range(i - 1, -1, -1))
            for k in order:
                if _overlay_layouts.get(key) is not L:  # 图层又改了：这一份作废，不用再算
                    return
                if k not in L.zmaps:
                    with (lock or threading.Lock()):
                        if k not in L.zmaps:
                            L.zmaps[k] = L.zone_fn(k)
        L.rect_s = [round(v * L.s) for v in frame_rect(project)]

        def zf(k):                                       # 扩图：叠加层也按画框大小算（和 frame_one 一样）
            z = L.zones_i(k)[0]
            if L.rect_s == [0, 0, L.w, L.h]:
                return z
            x, y, fw, fh = L.rect_s
            out = to_frame(z, L.rect_s, EMPTY)           # 原片外 = 空区
            outside = to_frame(np.zeros_like(z), L.rect_s, 1) > 0
            cr = project.crop_rows() if hasattr(project, "crop_rows") else None
            if cr:                                       # 裁切黑边：黑边那几行也算原片外（和 frame_one 一样）
                outside[:max(0, round(cr[0] * L.s) - y)] = True
                outside[max(0, round(cr[1] * L.s) - y):] = True
            for o in L.moved:                            # 移到原片外的人 = 本体（不是空区）
                m = L.cmask(o["id"], k)
                if m.any():
                    A = (np.array([[1, 0, -x], [0, 1, -y], [0, 0, 1]], np.float64)
                         @ np.vstack([L.Ms[o["id"]], [0, 0, 1]]))[:2].astype(np.float32)
                    out[outside & (cv2.warpAffine(m.astype(np.uint8), A, (fw, fh), flags=cv2.INTER_NEAREST) > 0)] = BODY
            return out
        L.zone_fn = zf
        threading.Thread(target=warm, daemon=True).start()
    if i not in L.zmaps:
        L.zmaps[i] = L.zone_fn(i)
    return L.zmaps[i]


def rect_blocks(b, k):
    """空区改成方块：横切成至多 k 段，每段换成外接长方形（k = 0 原样返回）。视频的洞、背景图补洞共用。
    切在哪里用动态规划选：k 段方块的总面积最小（同样段数下遮掉的背景最少，切点自然落在脖子、腰这些宽窄变化处）。"""
    if k <= 0 or not b.any():
        return b
    rows = np.nonzero(b.any(1))[0]
    y0, y1 = rows.min(), rows.max() + 1
    n = y1 - y0
    sub = b[y0:y1]
    has = sub.any(1)
    lo = np.where(has, sub.argmax(1), np.iinfo(np.int32).max).astype(np.int64)
    hi = np.where(has, sub.shape[1] - 1 - sub[:, ::-1].argmax(1), -1).astype(np.int64)
    cost = np.full((n + 1, n + 1), np.inf)               # cost[a, c] = 第 a..c-1 行合成一段的方块面积
    for a in range(n):
        mn, mx = np.minimum.accumulate(lo[a:]), np.maximum.accumulate(hi[a:])
        cost[a, a + 1:] = np.arange(1, n - a + 1) * np.maximum(mx - mn + 1, 0)
    dp, arg = [np.full(n + 1, np.inf)], []
    dp[0][0] = 0
    for _ in range(min(k, n)):                           # dp[j][c] = 前 c 行切成 j 段的最小面积
        tot = dp[-1][:, None] + cost
        arg.append(tot.argmin(0))
        dp.append(tot.min(0))
    j = int(np.argmin([d[n] for d in dp[1:]])) + 1        # 段数可以少于 k（再多切也不会更小时）
    cuts, c = [n], n
    for jj in range(j, 0, -1):
        c = int(arg[jj - 1][c])
        cuts.append(c)
    cuts = cuts[::-1]
    out = np.zeros_like(b)
    for a, c in zip(cuts[:-1], cuts[1:]):
        xs = np.nonzero(sub[a:c].any(0))[0]
        if len(xs):
            out[y0 + a:y0 + c, xs.min():xs.max() + 1] = True
    return out


def frame_rect(project):
    """画框（输出画面）在原片坐标里的位置 [x, y, w, h]。扩图时比原片大。"""
    fr = project.data.get("frame") or {}
    return [int(fr.get("x", 0)), int(fr.get("y", 0)), int(fr.get("w", project.data["w"])), int(fr.get("h", project.data["h"]))]


def to_frame(a, rect, fill=0):
    """原片大小的数组 → 画框大小（原片放到对应位置，原片外填 fill）。"""
    x, y, fw, fh = rect
    h, w = a.shape[:2]
    out = np.full((fh, fw) + a.shape[2:], fill, a.dtype)
    sx0, sy0, sx1, sy1 = max(x, 0), max(y, 0), min(x + fw, w), min(y + fh, h)
    if sx1 > sx0 and sy1 > sy0:
        out[sy0 - y:sy1 - y, sx0 - x:sx1 - x] = a[sy0:sy1, sx0:sx1]
    return out


def frame_one(L, i, comp_i, z_i, rect):
    """一帧从原片大小换到画框大小（扩图）：原片外 = 空区（涂灰 128、完全重绘）；图层在画框坐标里重新贴，
    移出原片的人不被截掉（算本体，不是空区）。返回 (画框大小的合成图, 分区, 所有人盖住的地方)。"""
    H, W = comp_i.shape[:2]
    outside = to_frame(np.zeros((H, W), np.uint8), rect, 1) > 0
    cr = L.project.crop_rows() if hasattr(L.project, "crop_rows") else None
    if cr:                                               # 裁切黑边：黑边那几行也算原片外（图层移动 / 放大伸进去的部分是本体，不重绘）
        k = H / float(L.project.data["h"])
        y0 = int(round(rect[1] * k))
        outside[:max(0, int(round(cr[0] * k)) - y0)] = True
        outside[max(0, int(round(cr[1] * k)) - y0):] = True
    filled = to_frame(comp_i, rect, 128)
    cov, cov_moved = L.paint_framed(filled, i, rect)
    zf = to_frame(z_i, rect, EMPTY)
    zf[outside] = EMPTY
    zf[outside & cov_moved] = BODY
    return filled, zf, cov


def compose_still(project, i, layout=False):
    """单帧粗合成（首帧 / 尾帧标签用）：返回 (合成图, 分区)。抠走的地方（原位置的团块、缺口）一律灰 128 = 空，
    不铺背景图、不模糊、不用别的帧填；只有出候选时 Qwen 才补（空就是空）。"""
    L = _Layout(project, {}, allow_unchanged=True)
    z, empty = L.zones_i(i)
    comp = L.frame(i).copy()
    L.paint_i(comp, i, empty)
    return (comp, z, L) if layout else (comp, z)
