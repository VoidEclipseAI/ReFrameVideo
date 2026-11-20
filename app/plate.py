"""干净背景板：把“去除”的空洞先用真实背景填上，再交给 H3 低去噪协调。

1. 每帧对齐到关键帧（只用背景上的特征点，单应性；远景或摄影机原地转动时成立）
2. 去掉所有物体后，对齐后的各帧取中位数 → 全景背景板（原片里露出过的真实像素）
3. 整段都没露出过的洞：用图像模型（Qwen Image）修好的关键帧补；再不行用 OpenCV
4. 背景板按每帧的摄影机运动变换回去，只替换目标物体遮罩里的区域
"""
import warnings

import cv2
import numpy as np


def _dilate(m, px):
    return cv2.dilate(m.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1,) * 2)) > 0


def _pan_like(H, w, h, tol=1.25):
    """单应性像不像摄影机摇 / 推：变换后四条边的缩放比例彼此相差不超过 tol 倍（透视拉伸不大）。
    近处的山和远处的山视差不一样时，单应性会被扯成一边几千像素的梯形，面积却可能还在 _sane 的 4 倍以内
    （1009 片段第 21–34 帧），拼进全景图就把人和山抹成一大片。"""
    q = cv2.perspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2), H).reshape(4, 2)
    if not np.isfinite(q).all() or not cv2.isContourConvex(q.reshape(-1, 1, 2)):
        return False
    r = np.linalg.norm(q - np.roll(q, -1, 0), axis=1) / np.array([w, h, w, h], float)
    return r.min() > 0 and r.max() / r.min() < tol


def homographies(frames, fg, ref, log=print, with_status=False):
    """每帧像素 → 关键帧像素的单应性。fg (N,H,W)：前景（所有物体，已外扩），不参与匹配。
    单应性被视差扯歪时（不像摇 / 推），改用同一批匹配点拟合的相似变换（平移 + 旋转 + 等比缩放），更稳。"""
    h, w = frames.shape[1:3]
    sift = cv2.SIFT_create(4000)
    matcher = cv2.BFMatcher()
    # 先做局部直方图均衡再找特征点：暗场（夜景平均亮度 15/255）原图只找得到几个点，97 帧里 96 帧对不上（f01d，2026-11-07）
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray = [clahe.apply(cv2.cvtColor(f, cv2.COLOR_RGB2GRAY)) for f in frames]
    feats =[sift.detectAndCompute(g, (~fg[i]).astype(np.uint8) * 255) for i, g in enumerate(gray)]

    def match(i, j):
        (ki, di), (kj, dj) = feats[i], feats[j]
        if di is None or dj is None:
            return None
        good = [p[0] for p in matcher.knnMatch(di, dj, k=2) if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
        if len(good) < 15:
            return None
        src = np.float32([ki[m.queryIdx].pt for m in good])
        dst = np.float32([kj[m.trainIdx].pt for m in good])
        Hm, inl = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
        if Hm is not None and inl.sum() >= 12 and _pan_like(Hm, w, h):
            return Hm
        A, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
        if A is None or inl.sum() < 12:
            return None
        bent.append((i, j))
        return np.vstack([A, [0, 0, 1]])

    n = len(frames)
    reliable = np.ones(n, dtype=bool)
    bent = []
    Hs = np.tile(np.eye(3), (n, 1, 1))
    for i in list(range(ref + 1, n)) + list(range(ref - 1, -1, -1)):
        nb = i - 1 if i > ref else i + 1
        Hm = match(i, ref)
        if Hm is None:                                   # 和关键帧直接匹配失败：和已对齐的相邻帧匹配再串联
            Hn = match(i, nb)
            Hm = Hs[nb] @ Hn if Hn is not None else Hs[nb]
            reliable[i] = Hn is not None and reliable[nb]
            if Hn is None and not with_status:
                log(f"  第 {i} 帧对齐失败，沿用相邻帧")
        if not _pan_like(Hm, w, h):                      # 串联下来还是歪的：沿用相邻帧
            reliable[i] = False
            if not with_status:
                log(f"  第 {i} 帧对齐结果变形太大，沿用相邻帧")
            Hm = Hs[nb]
        Hs[i] = Hm
    if bent:
        log(f"  {len({a for a, _ in bent})} 帧的单应性被视差扯歪，改用相似变换："
            f"{sorted({a for a, _ in bent})[:12]}{'…' if len(bent) > 12 else ''}")
    return (Hs, reliable) if with_status else Hs


def _nanmedian(warped, valid):
    PH = warped.shape[1]
    plate = np.zeros(warped.shape[1:], np.uint8)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for r in range(0, PH, 64):
            blk = warped[:, r:r + 64].astype(np.float32)
            blk[~valid[:, r:r + 64]] = np.nan
            plate[r:r + 64] = np.nan_to_num(np.nanmedian(blk, axis=0)).astype(np.uint8)
    return plate


def _compensate_exposure(warped, valid, plate, down=8, sigma_frac=0.08, lo=0.6, hi=1.6):
    """曝光补偿（去接缝）：每帧算一张平滑的增益图，把它的亮度/颜色拉到和全景中位数一致，再重新取中位数。
    接缝的来源：各帧曝光不同、有暗角，取中位数时参与的帧一换，亮度就跳一下。
    增益图是低频的（模糊半径约全景的 8%），所以能修整体曝光漂移和暗角，不会改纹理细节。原地修改 warped。"""
    k, PH, PW = valid.shape
    sw, sh = max(1, PW // down), max(1, PH // down)
    ref = cv2.resize(plate, (sw, sh), interpolation=cv2.INTER_AREA).astype(np.float32)
    sig = sigma_frac * max(sw, sh)
    for j in range(k):
        v = cv2.resize(valid[j].astype(np.float32), (sw, sh), interpolation=cv2.INTER_AREA)
        v = (v > 0.99).astype(np.float32)                  # 只用整块都有效的小格，边缘的黑边不算
        if v.sum() < 50:
            continue
        cur = cv2.resize(warped[j], (sw, sh), interpolation=cv2.INTER_AREA).astype(np.float32)
        wv = cv2.GaussianBlur(v, (0, 0), sig)[..., None] + 1e-6
        num = cv2.GaussianBlur(ref * v[..., None], (0, 0), sig) / wv
        den = cv2.GaussianBlur(cur * v[..., None], (0, 0), sig) / wv
        g = np.clip((num + 4) / (den + 4), lo, hi)
        g = cv2.resize(g, (PW, PH), interpolation=cv2.INTER_LINEAR)
        m = valid[j]
        warped[j][m] = np.clip(warped[j][m].astype(np.float32) * g[m], 0, 255).astype(np.uint8)


def build_plate(frames, fg, Hs, stride=2, edge_px=3, with_count=False, exposure=True):
    """返回 (全景板, 有效区, 平移矩阵 T, 全景尺寸[, 每个像素的有效样本数])。全景坐标 = T @ Hs[i] @ 帧坐标。
    每帧的有效区往里收 edge_px 像素：变形时帧边框会混进黑边，前景边缘也不干净。
    exposure：先取一次中位数当参照，做曝光补偿后再取一次，去掉帧与帧之间亮度不同造成的接缝。"""
    n, h, w = frames.shape[:3]
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    allc = np.concatenate([cv2.perspectiveTransform(corners, Hs[i]) for i in range(n)])
    x0, y0 = np.floor(allc.min((0, 1))).astype(int)
    x1, y1 = np.ceil(allc.max((0, 1))).astype(int)
    T = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], float)
    PW, PH = int(x1 - x0), int(y1 - y0)
    idx = list(range(0, n, stride))
    warped = np.zeros((len(idx), PH, PW, 3), np.uint8)
    valid = np.zeros((len(idx), PH, PW), bool)
    for j, i in enumerate(idx):
        M = T @ Hs[i]
        warped[j] = cv2.warpPerspective(frames[i], M, (PW, PH), flags=cv2.INTER_LINEAR)
        v = cv2.warpPerspective((~fg[i]).astype(np.uint8) * 255, M, (PW, PH), flags=cv2.INTER_NEAREST)
        if edge_px:
            v = cv2.erode(v, np.ones((2 * edge_px + 1, 2 * edge_px + 1), np.uint8))
        valid[j] = v > 127
    plate = _nanmedian(warped, valid)
    if exposure:
        _compensate_exposure(warped, valid, plate)       # 一轮就够（实测两轮看不出区别，慢一倍）
        plate = _nanmedian(warped, valid)
    if with_count:
        return plate, valid.any(0), T, (PW, PH), valid.sum(0)
    return plate, valid.any(0), T, (PW, PH)


def match_colors(src, ref, region, ring, sigma=60):
    """把 src 在 region 内的低频亮度/颜色对齐到 ref：在 ring（可靠的参照像素）上算两者之差，
    用归一化高斯卷积平滑扩散到 region 内部。参照像素缺失的地方（比如碰到画面边缘）自动由附近的补上。"""
    if not ring.any():
        return src.copy()
    h, w = ring.shape
    s = 8                                                        # 在 1/8 分辨率上算，快且足够平滑
    diff = (ref.astype(np.float32) - src.astype(np.float32)) * ring[..., None]
    small = lambda a: cv2.resize(a, (w // s, h // s), interpolation=cv2.INTER_AREA)
    num = cv2.GaussianBlur(small(diff), (0, 0), sigma / s)
    den = cv2.GaussianBlur(small(ring.astype(np.float32)), (0, 0), sigma / s)[..., None]
    glob = diff.reshape(-1, 3).sum(0) / ring.sum()               # 离参照太远的地方用整体平均差
    a = np.clip(den / 1e-2, 0, 1)
    offset = a * num / np.maximum(den, 1e-6) + (1 - a) * glob
    offset = cv2.resize(offset, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.clip(src.astype(np.float32) + offset, 0, 255).astype(np.uint8)


def blob(mask, px, s=4):
    """把人形遮罩外扩后再做大核闭运算，变成没有“人形”轮廓的团块（避免 H3 顺着形状画出人来）。
    在 1/s 分辨率上算：团块本来就是粗略形状，结果和全分辨率重合 99.5%，快 100 多倍（全分辨率一帧 1.5 秒）。"""
    if not mask.any():
        return mask.copy()
    h, w = mask.shape
    sm = cv2.resize(mask.astype(np.uint8), (max(w // s, 1), max(h // s, 1)), interpolation=cv2.INTER_AREA) > 0
    r = max(1, round(px / s))
    m = cv2.dilate(sm.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1,) * 2))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (4 * r + 1,) * 2))
    return cv2.resize(m * 255, (w, h), interpolation=cv2.INTER_LINEAR) > 127


# ---------- 背景全景图：把整段视频的背景整合成一张大图 ----------

class AlignmentError(Exception):
    def __init__(self, pw, ph):
        super().__init__(f"{pw}x{ph}")
        self.size = (pw, ph)


def _sane(H, w, h):
    """单应性是否像“摄影机摇/推”：变形后的画面仍是凸四边形，面积在原画面的 1/4 ~ 4 倍之间。
    对齐算崩了的帧会被拉成巨大或翻折的形状。"""
    q = cv2.perspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2), H)
    if not np.isfinite(q).all():
        return False
    area = abs(cv2.contourArea(q))
    return cv2.isContourConvex(q) and 0.25 * w * h < area < 4 * w * h


def panorama(frames, fg, ref=None, stride=2, align_scale=0.5, min_samples=4, max_area=4, mem_bytes=3e9, log=print):
    """frames (N,H,W,3)；fg (N,H,W) bool：要排除的前景（已外扩）。
    对齐在 align_scale 分辨率上算（快），再换算回原分辨率拼图。
    返回 dict：plate 全景图、valid 有真实背景的像素、covered 原片拍到过的范围、
    holes 拍到过但始终被前景挡住的洞、T/Hs 坐标变换（全景 = T @ Hs[i] @ 帧坐标）。"""
    n, h, w = frames.shape[:3]
    ref = n // 2 if ref is None else ref
    s = align_scale
    if s != 1:
        sw, sh = round(w * s), round(h * s)
        small = np.stack([cv2.resize(f, (sw, sh), interpolation=cv2.INTER_AREA) for f in frames])
        fg_small = np.stack([cv2.resize(m.astype(np.uint8), (sw, sh), interpolation=cv2.INTER_NEAREST) > 0 for m in fg])
        log(f"  对齐各帧（{sw}x{sh}）…")
        Hs_small = homographies(small, fg_small, ref, log)
        S = np.diag([sw / w, sh / h, 1.0])
        Hs = np.array([np.linalg.inv(S) @ H @ S for H in Hs_small])
    else:
        log("  对齐各帧…")
        Hs = homographies(frames, fg, ref, log)
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    allc = np.concatenate([cv2.perspectiveTransform(corners, H) for H in Hs]).reshape(-1, 2)
    span = allc.max(0) - allc.min(0)
    bad = sum(not _sane(H, w, h) for H in Hs)
    # 对齐失败的典型表现：有帧被拉成离谱的形状、拼出来大得离谱。先停下，不去拼（会占几十 GB 甚至 TB 内存）
    # 用浮点算面积：Windows 上 numpy 的 int 是 32 位，大数相乘会溢出成小数字
    if bad or not np.isfinite(span).all() or float(span[0]) * float(span[1]) > max_area * w * h:
        raise AlignmentError(*(int(min(v, 1e9)) if np.isfinite(v) else -1 for v in span))
    PW, PH = int(span[0]), int(span[1])
    stride = max(stride, int(np.ceil(n * PW * PH * 3 * 2 / mem_bytes)))   # 全景很大时少取几帧，内存不超过 mem_bytes
    log("  拼接全景、曝光补偿、取中位数…")
    plate, ok, T, (PW, PH), count = build_plate(frames, fg, Hs, stride=stride, with_count=True)
    covered = np.zeros((PH, PW), bool)
    ones = np.full((h, w), 255, np.uint8)
    for i in range(0, n, stride):
        covered |= cv2.warpPerspective(ones, T @ Hs[i], (PW, PH), flags=cv2.INTER_NEAREST) > 127
    # 只有少数几帧拍到过的像素不可靠（多在全景边缘：云在动，几帧对不上，取中位数后成阶梯）
    weak = ok & (count < min_samples)
    return dict(plate=plate, valid=ok & ~weak, covered=covered, holes=covered & ~ok, weak=weak, count=count,
                T=T, Hs=Hs, size=(PW, PH), ref=ref)


def smooth_homographies(T, Hs, size, med=7, sigma=3.0):
    """全景图每帧的变换沿时间平滑：逐帧单应性是各自匹配特征点拟合的，彼此有几像素的随机误差（1009：画面四角在全景图上
    逐帧跳动中位 9 像素，个别帧跳 300 像素），0.98 铺进画面就是背景抖动。做法：把每帧画面四个角投到全景图上，
    四角轨迹先做中值滤波（去掉个别跳飞的帧）再高斯平滑，按平滑后的四角重新算每帧的变换。med / sigma 单位是帧，sigma=0 不平滑。"""
    from scipy.ndimage import gaussian_filter1d, median_filter
    Hs = np.asarray(Hs, np.float64)
    if sigma <= 0 or len(Hs) < 3:
        return Hs
    w, h = size
    T = np.asarray(T, np.float64)
    c = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    tr = np.stack([cv2.perspectiveTransform(c, T @ H).reshape(8) for H in Hs])            # (n, 8) 四角在全景图上的位置
    tr = median_filter(tr, size=(min(med, len(Hs)), 1), mode="nearest")
    tr = gaussian_filter1d(tr, sigma, axis=0, mode="nearest")
    Ti = np.linalg.inv(T)
    return np.stack([Ti @ cv2.getPerspectiveTransform(c.reshape(4, 2), q.reshape(4, 2).astype(np.float32)) for q in tr])


def pano_to_frame(pano, T, H, size, scale=1.0, offset=(0, 0), border=None):
    """把全景图按第 i 帧的摄影机运动变换回那一帧的画面坐标。size = 输出范围 (w, h)（画面坐标，左上角在 offset）；
    scale 用于直接出小图预览。border=None 时边外反射填充，否则用这个颜色填。"""
    w, h = size
    S = np.array([[scale, 0, -offset[0] * scale], [0, scale, -offset[1] * scale], [0, 0, 1.0]])
    M = S @ np.linalg.inv(np.asarray(T) @ np.asarray(H))
    kw = dict(borderMode=cv2.BORDER_REFLECT) if border is None else dict(borderMode=cv2.BORDER_CONSTANT, borderValue=border)
    return cv2.warpPerspective(pano, M, (round(w * scale), round(h * scale)), flags=cv2.INTER_LINEAR, **kw)


def pano_extent(T, Hs, pano_size, frame_size, stride=5):
    """全景图在画面坐标里的范围（所有帧的并集），限制在原画面四周各一倍以内。返回 [x0, y0, x1, y1]。"""
    PW, PH = pano_size
    w, h = frame_size
    c = np.float32([[0, 0], [PW, 0], [PW, PH], [0, PH]]).reshape(-1, 1, 2)
    pts = np.concatenate([cv2.perspectiveTransform(c, np.linalg.inv(np.asarray(T) @ np.asarray(Hs[i])))
                          for i in range(0, len(Hs), stride)]).reshape(-1, 2)
    x0, y0 = np.maximum(pts.min(0), (-w, -h))
    x1, y1 = np.minimum(pts.max(0), (2 * w, 2 * h))
    return [int(np.floor(x0)), int(np.floor(y0)), int(np.ceil(x1)), int(np.ceil(y1))]


def reframe(frame, pano, T, H, rect, others_mask=None):
    """画框：rect = (x, y, w, h) 画面坐标。框里原片有的地方用原片，原片外的地方用全景图补，
    全景图的颜色按原片边缘一圈对齐。others_mask：物体（不当颜色参照）。"""
    x, y, fw, fh = rect
    h, w = frame.shape[:2]
    out = pano_to_frame(pano, T, H, (fw, fh), offset=(x, y), border=(0, 0, 0)) if pano is not None \
        else np.zeros((fh, fw, 3), np.uint8)
    sx0, sy0, sx1, sy1 = max(x, 0), max(y, 0), min(x + fw, w), min(y + fh, h)
    if sx1 <= sx0 or sy1 <= sy0:
        return out
    dx0, dy0 = sx0 - x, sy0 - y
    win = (slice(dy0, dy0 + sy1 - sy0), slice(dx0, dx0 + sx1 - sx0))
    src = np.zeros_like(out)
    src[win] = frame[sy0:sy1, sx0:sx1]
    inside = np.zeros((fh, fw), bool)
    inside[win] = True
    if inside.all():
        return src
    if pano is not None:
        ring = inside & ~cv2.erode(inside.astype(np.uint8), np.ones((81, 81), np.uint8)).astype(bool)
        if others_mask is not None:
            om = np.zeros((fh, fw), bool)
            om[win] = others_mask[sy0:sy1, sx0:sx1]
            ring &= ~om
        out = match_colors(out, src, ~inside, ring, sigma=120)
    a = cv2.GaussianBlur(cv2.erode(inside.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(np.float32), (0, 0), 3)
    a = np.where(inside, a, 0)[..., None]                          # 原片边缘往里羽化几个像素，接缝不硬
    return (src * a + out * (1 - a)).clip(0, 255).astype(np.uint8)


def mosaic(frames, fg, ref=None, max_err=3.0, max_area=9, align_scale=0.5, edge_px=3, seam_px=4, seam_jump=12, log=print):
    """“以参考帧为准”的拼接（不取中位数，没有拖影）：
    参考帧（中间三分之一里物体最少的一帧）原样铺上；然后按离参考帧的远近一帧帧往外补——
    每帧只补还空着的地方（物体挡住的、画面外的），并且要和已铺好的部分对得上（重叠区亮度误差中位数 ≤ max_err），
    对不上的帧（视差、运动模糊、对齐失败）连同更远的帧都不用。补之前按重叠区做曝光补偿。
    往前推 / 往前飞的镜头远处的帧会被拒，结果接近单帧；摇镜头照样拼成宽全景。
    seams：不同帧补进来的区域之间的边界，往两边各扩 seam_px —— 拼接缝（亮度跳变、错位、细线）都在这里，
    交给 Qwen 一起重绘（放在 weak 里返回）。
    返回和 panorama() 一样的 dict，另加 used（用到的帧号）、src（每个像素来自哪一帧，-1 = 没铺到）。"""
    n, h, w = frames.shape[:3]
    if ref is None:
        mid = range(n // 3, max(n // 3 + 1, 2 * n // 3))
        ref = min(mid, key=lambda i: int(fg[i].sum()))
    s = align_scale
    sw, sh = round(w * s), round(h * s)
    small = np.stack([cv2.resize(f, (sw, sh), interpolation=cv2.INTER_AREA) for f in frames])
    fg_small = np.stack([cv2.resize(m.astype(np.uint8), (sw, sh), interpolation=cv2.INTER_NEAREST) > 0 for m in fg])
    S = np.diag([sw / w, sh / h, 1.0])
    Hs = np.array([np.linalg.inv(S) @ H @ S for H in homographies(small, fg_small, ref, log)])
    order = sorted(range(n), key=lambda i: abs(i - ref))
    # 画布：从参考帧往外加帧，直到面积超上限；变形离谱的帧不要
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    cand, pts = [], []
    for i in order:
        if not _sane(Hs[i], w, h):
            continue
        p = cv2.perspectiveTransform(corners, Hs[i]).reshape(-1, 2)
        allp = np.concatenate(pts + [p])
        span = allp.max(0) - allp.min(0)
        if float(span[0]) * float(span[1]) > max_area * w * h:
            continue
        cand.append(i)
        pts.append(p)
    allp = np.concatenate(pts)
    x0, y0 = np.floor(allp.min(0)).astype(int)
    x1, y1 = np.ceil(allp.max(0)).astype(int)
    T = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], float)
    PW, PH = int(x1 - x0), int(y1 - y0)
    plate = np.zeros((PH, PW, 3), np.uint8)
    filled = np.zeros((PH, PW), bool)
    covered = np.zeros((PH, PW), bool)
    src = np.full((PH, PW), -1, np.int32)
    inner = cv2.erode(np.full((h, w), 255, np.uint8), np.ones((2 * edge_px + 1,) * 2, np.uint8))
    stopped = {-1: False, 1: False}                         # 某一侧出现对不上的帧，就不再往那一侧走
    used, errs = [], {}
    for i in cand:
        side = 1 if i > ref else -1
        if i != ref and stopped[side]:
            continue
        M = T @ Hs[i]
        foot = cv2.warpPerspective(inner, M, (PW, PH), flags=cv2.INTER_NEAREST) > 127
        valid = foot & (cv2.warpPerspective((~fg[i]).astype(np.uint8) * 255, M, (PW, PH), flags=cv2.INTER_NEAREST) > 127)
        wf = cv2.warpPerspective(frames[i], M, (PW, PH), flags=cv2.INTER_LINEAR)
        if i != ref:
            ov = valid & filled
            ov = cv2.erode(ov.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
            if ov.sum() < 2000:                              # 和已铺好的部分几乎不重叠：没法判断，不用
                stopped[side] = True
                continue
            a = cv2.GaussianBlur(cv2.cvtColor(wf, cv2.COLOR_RGB2GRAY), (0, 0), 2).astype(np.float32)
            b = cv2.GaussianBlur(cv2.cvtColor(plate, cv2.COLOR_RGB2GRAY), (0, 0), 2).astype(np.float32)
            gain = np.clip(np.median(plate[ov].astype(np.float32), 0) / np.maximum(np.median(wf[ov].astype(np.float32), 0), 1),
                           0.8, 1.25)
            err = float(np.median(np.abs(a * float(gain.mean()) - b)[ov]))
            errs[i] = round(err, 2)
            if err > max_err:
                stopped[side] = True
                continue
            wf = np.clip(wf.astype(np.float32) * gain, 0, 255).astype(np.uint8)
        new = valid & ~filled
        plate[new] = wf[new]
        src[new] = i
        filled |= new
        covered |= foot
        used.append(i)
    log(f"  以第 {ref} 帧为准，用了 {len(used)} 帧（{min(used)}~{max(used)}）")
    # 只保留真正拍到的范围（被拒的帧算进画布的部分裁掉）
    ys, xs = np.where(covered)
    cx0, cy0, cx1, cy1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    T = np.array([[1, 0, -cx0], [0, 1, -cy0], [0, 0, 1]], float) @ T
    plate, filled, covered = plate[cy0:cy1, cx0:cx1], filled[cy0:cy1, cx0:cx1], covered[cy0:cy1, cx0:cx1]
    src = src[cy0:cy1, cx0:cx1]
    PH, PW = filled.shape
    # 拼接缝：相邻像素来自不同的帧（两边都铺到了）
    # 只要看得见的缝：缝两边（隔 2 像素）亮度差超过 seam_jump，才算（对得很好的缝不动真实像素）
    g = cv2.GaussianBlur(cv2.cvtColor(plate, cv2.COLOR_RGB2GRAY), (0, 0), 1).astype(np.float32)
    edge = np.zeros_like(filled)
    edge[:, 2:] |= (src[:, 2:] != src[:, :-2]) & filled[:, 2:] & filled[:, :-2] & (np.abs(g[:, 2:] - g[:, :-2]) > seam_jump)
    edge[2:, :] |= (src[2:, :] != src[:-2, :]) & filled[2:, :] & filled[:-2, :] & (np.abs(g[2:, :] - g[:-2, :]) > seam_jump)
    seams = (cv2.dilate(edge.astype(np.uint8), np.ones((2 * seam_px + 1,) * 2, np.uint8)) > 0) & filled
    return dict(plate=plate, valid=filled & ~seams, covered=covered, holes=covered & ~filled, weak=seams, src=src,
                count=filled.astype(np.int32), T=T, Hs=Hs, size=(PW, PH), ref=ref, used=sorted(used), errs=errs)


LETTERBOX_LUMA = 10      # 这一行在所有抽样帧里都比这个暗（平均亮度；最亮的 2% 像素 < 3 倍）= 黑边。真黑边约 0，暗场景约 20（第 3 段）


def detect_letterbox(frames):
    """电影上下的黑边：从上 / 下边往里数，每一行在所有抽样帧里都几乎全黑就算黑边。返回 (上, 下) 行数，取偶数。
    只看上下（宽银幕电影的遮幅），整幅都黑（比如黑场）时返回 (0, 0)。"""
    g = np.stack([cv2.cvtColor(f, cv2.COLOR_RGB2GRAY) for f in frames]).astype(np.float32)   # (帧, 高, 宽)
    dark = (g.mean(2).max(0) < LETTERBOX_LUMA) & (np.percentile(g, 98, axis=2).max(0) < LETTERBOX_LUMA * 3)
    h = len(dark)
    if dark.all():
        return 0, 0
    top = int(np.argmin(dark))                           # 第一行不是黑边的位置
    bottom = int(np.argmin(dark[::-1]))
    if top + bottom > h * 0.6:                           # 剩下不到 40%：多半是暗场景，不是黑边
        return 0, 0
    return top + top % 2, bottom + bottom % 2            # 往里取偶数（视频编码要偶数尺寸），黑边边上那一两行发灰的也去掉
