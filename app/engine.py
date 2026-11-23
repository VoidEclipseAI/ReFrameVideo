"""引擎：分割（SAM 3.1）和局部重绘（H3）。

MockEngine  —— 笔记本上测界面用，不需要 GPU：分割返回一个移动的椭圆，重绘用 OpenCV 补洞。
ComfyEngine —— 通过 HTTP 调用 ComfyUI（可以是局域网里的 4090）。工作流在代码里搭好再提交，
               不依赖导出的 JSON；输入文件上传、结果下载都走 API。
"""
import json
import math
import re
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np
from PIL import Image
import requests

from project import FFMPEG, read_video, write_video


# ---------- 停止：用户点「停止」时，正在跑的 ComfyUI 任务要立刻撤掉 ----------
CANCEL = threading.Event()


class Cancelled(RuntimeError):
    """用户点了停止。"""


def request_cancel():
    CANCEL.set()


def clear_cancel():
    CANCEL.clear()


def check_cancel():
    if CANCEL.is_set():
        raise Cancelled("已停止")

try:                                    # ComfyUI 的进度（第几步 / 共几步）走 websocket；没装这个库就只显示阶段和用时
    import websocket
except ImportError:
    websocket = None

# ComfyUI 节点 → 界面上显示的阶段名
STAGES = {
    "UNETLoader": "加载模型", "CLIPLoader": "加载模型", "VAELoader": "加载模型", "LoraLoaderModelOnly": "加载 LoRA",
    "CheckpointLoaderSimple": "加载模型", "QwenImage21Cache": "加载模型",
    "LoadImage": "读入素材", "LoadVideo": "读入素材", "GetVideoComponents": "读入素材",
    "TextEncodeQwenImage21": "编码提示词和参考图", "MiniMaxH3ReferenceToVideo": "编码提示词和参考图",
    "CLIPTextEncode": "编码提示词", "VAEEncode": "编码画面",
    "KSampler": "采样", "SamplerCustomAdvanced": "采样", "VAEDecode": "解码画面",
    "SAM3_VideoTrack": "SAM 3.1 逐帧识别", "SAM3_TrackToMask": "SAM 3.1 生成遮罩",
    "TextGenerate": "看图写描述", "CreateVideo": "保存结果", "SaveVideo": "保存结果", "SaveImage": "保存结果",
    # 下面这些很快，只是不要把节点原名露给界面
    "LTXVSeparateAVLatent": "整理生成结果", "LTXVConcatAVLatent": "准备采样", "SetLatentNoiseMask": "准备采样",
    "ImageToMask": "读入遮罩", "MaskToImage": "读入遮罩", "ThresholdMask": "读入遮罩", "ImageFromBatch": "读入素材",
    "ReframeH3Mask": "换算重绘区", "ReframeH3InpaintMask": "换算重绘区", "GetImageSize": "读入素材", "EmptyLatentImage": "准备采样",
    "RandomNoise": "准备采样", "BasicGuider": "准备采样", "BasicScheduler": "准备采样", "KSamplerSelect": "准备采样",
    "PreviewAny": "取出文字", "MatAnyone2": "MatAnyone2 抠柔边", "SAM3_Detect": "SAM 3.1 识别",
}


def fmt_secs(t):
    t = int(max(t, 0))
    return f"{t // 60} 分 {t % 60:02d} 秒" if t >= 60 else f"{t} 秒"

# ---------- H3 的几何规则（与 ComfyUI comfy_extras/nodes_minimax_h3.py 一致）----------
H3_SHORT, H3_MAX_PIXELS = 768, 768 * 1344
H3_SHORT_DEFAULT = 768           # 768p 预设（= H3 官方默认画布）用这个短边
H3_SHORTS = (480, 544, 640, 720, 768, 864, 1024, 1088)   # 「短边缩放」可选的短边（存 app_settings.json 的 h3_short）
# 生成分辨率三种模式（2026-11-15 用户定）：preset = 分辨率预设；short = 短边缩放；free = 自由分辨率（宽高上限）。
# 旧设置里的 auto（原默认）= 768p 预设。
H3_MODES = ("preset", "short", "free")
H3_MODE = "preset"
# 预设：标准画面框（横屏；竖屏宽高对调），按画幅比例整个放进框里，每边取 32 的倍数。768p = H3 官方默认画布（短边 768 + 面积上限，
# 官方称 1344×768 是训练尺寸）。官方没有 480p / 720p / 960p / 1080p 表；960p / 1080p 超出训练尺寸，没在 4090 上验证过。
H3_PRESETS = {"480": (854, 480), "540": (960, 540), "720": (1280, 720), "768": None, "960": (1706, 960), "1080": (1920, 1080)}
H3_PRESET = "768"
H3_FREE = (1344, 768)            # free 模式的宽高上限（按画幅比例放进去，每边 32 的倍数）
H3_FREE_MIN, H3_FREE_MAX = 256, 3840


def _snap32(v):
    return max(32, int(round(v / 32.0)) * 32)


def set_h3_short(v):
    """改 H3 画布的短边（面板里选）。h3_canvas 是模块级函数，改模块变量对 regions / generate 都生效。"""
    global H3_SHORT
    v = int(v or 0)
    if v not in H3_SHORTS:
        raise ValueError(f"短边只能是 {' / '.join(map(str, H3_SHORTS))}")
    H3_SHORT = v
    return H3_SHORT


def set_h3_mode(m):
    """preset（分辨率预设）/ short（短边缩放）/ free（自由分辨率）。旧的 auto = 768p 预设。"""
    global H3_MODE, H3_PRESET
    m = str(m or "").strip()
    if m == "auto":
        m, H3_PRESET = "preset", "768"
    if m not in H3_MODES:
        raise ValueError("分辨率模式只能是 preset / short / free")
    H3_MODE = m
    return H3_MODE


def set_h3_preset(v):
    global H3_PRESET
    v = str(v or "").strip().removesuffix("p")
    if v not in H3_PRESETS:
        raise ValueError(f"分辨率预设只能是 {' / '.join(k + 'p' for k in H3_PRESETS)}")
    H3_PRESET = v
    return H3_PRESET


def _snap32_tie(v, up):
    """取最近的 32 的倍数；正好在中间时 up=True 往上、否则往下（预设：短边不超过档位，长边取大的那个）。"""
    q = v / 32.0
    k = int(q) + (1 if q - int(q) > 0.5 or (q - int(q) == 0.5 and up) else 0)
    return max(32, k * 32)


def preset_canvas(w, h, preset):
    """分辨率预设的画布：标准画面框（竖屏对调）里按画幅比例放进去，每边 32 的倍数。768p 走官方默认规则。"""
    box = H3_PRESETS[preset]
    if box is None:
        return h3_canvas(w, h, short=H3_SHORT_DEFAULT)
    r = w / h
    bw, bh = box if r >= 1 else box[::-1]
    nw, nh = (bw, bw / r) if r >= bw / bh else (bh * r, bh)
    return (_snap32_tie(nw, up=r >= 1), _snap32_tie(nh, up=r < 1))


def set_h3_free(w, h):
    """free 模式的上限宽高（256–3840）。实际画布按画幅比例放进这个框里，再按 32 取整。"""
    global H3_FREE
    w, h = int(w or 0), int(h or 0)
    if not (H3_FREE_MIN <= w <= H3_FREE_MAX and H3_FREE_MIN <= h <= H3_FREE_MAX):
        raise ValueError(f"宽 / 高只能在 {H3_FREE_MIN} 到 {H3_FREE_MAX} 之间")
    H3_FREE = (w, h)
    return H3_FREE


def h3_canvas(w, h, short=None):
    """H3 画布：auto / short 走「短边 + 面积上限 768x1344」；free 走面板填的宽高上限（按画幅比例放进去）。
    显式传 short 时按 short 模式算（老调用点兼容）。每边取 32 的倍数。"""
    r = w / h
    if short is None and H3_MODE == "preset":
        return preset_canvas(w, h, H3_PRESET)
    if short is None and H3_MODE == "free":
        fw, fh = H3_FREE
        nw, nh = (fw, fw / r) if r >= fw / fh else (fh * r, fh)
        return _snap32(nw), _snap32(nh)
    if short is not None:                                     # 老的显式传参：按短边算
        s = int(short)
    else:                                                     # short（短边缩放）
        s = int(H3_SHORT)
    nw, nh = (s * r, s) if r >= 1 else (s, s / r)
    # 面积上限随短边一起放宽（只放宽、不收紧）：512 / 640 / 768 和以前完全一样，1024 才真的更大
    cap = max(H3_MAX_PIXELS, H3_MAX_PIXELS * (s / H3_SHORT_DEFAULT) ** 2)
    if nw * nh > cap:
        k = math.sqrt(cap / (nw * nh))
        nw, nh = nw * k, nh * k
    return _snap32(nw), _snap32(nh)


def h3_frames(n):
    """帧数补到 17k+5。"""
    n = max(5, n)
    while n % 17 != 5:
        n += 1
    return n


def h3_latent_groups(frames):
    """每个潜帧对应哪些像素帧：从第 0 帧起每 17 帧 → [1,4,4,4,4]，不满 17 帧的放在最后（158 帧 = 9×17 + 5 → 最后是 [1,4]）。
    2026-10-20 在 4090 上按 H3 VAE 的真实帧分组逐格核对过。以前写成“短块在前”是错的，
    mask 在时间上会错位几帧。"""
    groups, t = [], 0
    while t < frames:
        for s in (1, 4, 4, 4, 4):
            if t >= frames:
                break
            groups.append((t, min(t + s, frames)))
            t += s
    return groups


def h3_latent_mask(mask):
    """像素遮罩 (F, H, W) → 潜空间遮罩 (T, H/16, W/16)。格内、组内取 max：一个像素要重绘，整格都重绘；
    再对齐 H3 的 2x2 token 网格。与 ComfyUI 里 ReframeH3InpaintMask 的换算一致。"""
    f, h, w = mask.shape
    lh, lw = h // 16, w // 16
    cells = mask[:, :lh * 16, :lw * 16].reshape(f, lh, 16, lw, 16).any((2, 4))
    cells = cells.reshape(f, lh // 2, 2, lw // 2, 2).any((2, 4)).repeat(2, 1).repeat(2, 2)
    return np.stack([cells[a:b].any(0) for a, b in h3_latent_groups(f)])


def add_audio(video, src, fps, log=print):
    """给 video 配上 src 的音轨（不够长补静音）；src 没有音轨时配静音。NKDAVLatent 一定要有音频输入。"""
    video = Path(video)
    tmp = video.with_name(video.stem + "_a.mp4")
    base = [FFMPEG, "-v", "error", "-y", "-i", str(video)]
    tail = ["-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest", str(tmp)]
    ok = False
    if src:
        r = subprocess.run(base + ["-i", str(src), "-af", "apad"] + tail, capture_output=True)
        ok = r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0
    if not ok:
        log("  （原片没有音轨：配静音）")
        subprocess.run(base + ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"] + tail, capture_output=True, check=True)
    tmp.replace(video)
    return video


NO_EMOJI = "Use plain English words only, with no emoji or symbols."   # 回答会写进 H3 提示词：VLM 偶尔会加 emoji（2026-10-29「the 🌳 …」）


def no_emoji(question):
    """看图问答的问题末尾都加一句「不要 emoji」（ComfyUI 和 OpenAI 兼容 API 两种看图都用）。"""
    return question if NO_EMOJI in question else f"{question.rstrip()} {NO_EMOJI}"


def h3img_caption(txt):
    """H3 Image PE 的回答 → expanded_caption：去掉 <think> 那段，取最后一个 JSON 对象；解析不了就退回正则 / 原文。"""
    txt = re.sub(r"(?s)<think>.*?</think>", "", txt).strip()
    for m in reversed(list(re.finditer(r"\{", txt))):
        try:
            obj = json.loads(txt[m.start(): txt.rfind("}") + 1])
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("expanded_caption"):
            return " ".join(str(obj["expanded_caption"]).split())
    m = re.search(r'"expanded_caption"\s*:\s*"((?:[^"\\]|\\.)*)"', txt)
    return " ".join((json.loads(f'"{m.group(1)}"') if m else txt).split())


# 文件名里表示精度 / 量化的词（按 _ . - 切开后整段比对）：去掉这些剩下的一样 = 同一个模型的不同量化版
QUANT_WORDS = {"bf16", "fp16", "fp32", "fp8", "fp4", "e4m3fn", "e4m3", "e5m2", "e4m3fnuz", "scaled", "int8", "int4", "uint8",
               "convrot", "nvfp4", "mxfp8", "mxfp4", "gptq", "awq", "w4a8", "w8a8", "w4a16", "w8a16", "quant", "quantized",
               "pruned", "mixed", "safetensors", "sft", "ckpt", "pt", "pth", "bin"}


def model_base(name):
    """去掉精度 / 量化词后的模型名（qwen_image_2.1_turbo_fp8_e4m3fn.safetensors → qwen image 2 1 turbo）。"""
    words = re.split(r"[_.\-\s]+", Path(str(name)).name.lower())
    return " ".join(w for w in words if w and w not in QUANT_WORDS)


def same_model(default, have):
    """have 里和 default 是同一个模型（只差精度 / 量化）的文件；有几个时取名字排第一的，没有返回 None。"""
    base = model_base(default)
    hits = sorted(n for n in have if model_base(n) == base)
    return hits[0] if hits else None


def write_pixel_mask(path, vals, fps):
    """像素 mask 写成无损文件给 ComfyUI：vals = (F, H, W) uint8，每个像素的去噪强度 0~255（0 保留，255 完全重绘）。
    用 png 编码的 .mov，读回来每个值和写进去的一模一样（h264 会把 255 变成 254 之类，不能用）。
    潜空间的换算不在这里做：ComfyUI 里由 ReframeH3InpaintMask 按 H3 VAE 的真实帧分组和 2x2 token 网格来做。"""
    f, h, w = vals.shape
    proc = subprocess.Popen([FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                             "-r", str(fps), "-i", "-", "-c:v", "png", str(path)], stdin=subprocess.PIPE)
    for m in vals:
        proc.stdin.write(np.repeat(m[..., None], 3, 2).tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("写 mask 文件失败")
    return path


# ---------- SAM 跟踪后的清理：删掉突然冒出来、只闪几帧的碎块 ----------
# SAM 3.1 偶尔会在某一帧把远处一小块（比如一块山）并进物体，前后帧都没有。
# 做法：每帧的连通块（先外扩一点，离主体很近的算连着）沿时间和前后帧有重叠就连成一条「管道」。
# 一条管道总共出现不到 FRAG_MAX_FRAMES 帧、又不是最大的那条、而且它出现的每一帧里物体都还有别的长管道 → 是碎块，删掉。
# 只看几何，不管是什么物体；物体整个只出现几帧（比如刚进画面）不会被删，因为那几帧里没有别的长管道。
FRAG_LINK = 0.02          # 两块相距不到短边的 2% 算连着
FRAG_MAX_FRAMES = 6       # 只出现不到 6 帧（24fps 约 0.25 秒）的才可能是碎块


def _frame_labels(m, kernel):
    """一帧 mask 外扩后的连通块编号（0 = 背景）。"""
    return cv2.connectedComponents(cv2.dilate(m.astype(np.uint8), kernel), connectivity=8)


def drop_flicker_fragments(masks, link=FRAG_LINK, max_frames=FRAG_MAX_FRAMES):
    """masks: (n,h,w) bool，原地清理并返回 (masks, 删掉的帧号列表)。"""
    n, h, w = masks.shape
    r = max(1, round(min(h, w) * link / 2))            # 两边各外扩 r，相距 2r 以内的块连成一块
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    offs, pix = [None] * n, []                          # offs[t] = 这一帧编号的起点（全局编号 = 偏移 + 帧内编号）
    total, prev = 0, None
    parent = [0]
    for t in range(n):
        if not masks[t].any():
            prev = None
            continue
        c, lab = _frame_labels(masks[t], kernel)
        offs[t] = total
        cnt = np.bincount(lab[masks[t]], minlength=c)   # 每块里真正属于物体的像素数
        pix.extend(cnt[1:].tolist())
        parent.extend(range(total + 1, total + c))
        g = np.where(lab > 0, lab + total, 0)
        if prev is not None:                            # 和前一帧有重叠的块连起来
            both = (g > 0) & (prev > 0)
            for code in np.unique(g[both].astype(np.int64) * (total + c) + prev[both]):
                a, b = divmod(int(code), total + c)
                ra, rb = a, b
                while parent[ra] != ra:
                    ra = parent[ra]
                while parent[rb] != rb:
                    rb = parent[rb]
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
        total += c - 1
        prev = g
    if total <= 1:
        return masks, []

    def root(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    roots = [root(a) for a in range(total + 1)]
    frames, size = {}, {}
    for t in range(n):
        if offs[t] is None:
            continue
        nxt = next((offs[s] for s in range(t + 1, n) if offs[s] is not None), total)
        for a in range(offs[t] + 1, nxt + 1):
            if pix[a - 1]:
                frames.setdefault(roots[a], set()).add(t)
                size[roots[a]] = size.get(roots[a], 0) + pix[a - 1]
    biggest = max(size, key=size.get)
    keep = {k for k, f in frames.items() if len(f) >= max_frames} | {biggest}
    kept_frames = set().union(*(frames[k] for k in keep))
    drop = {k for k, f in frames.items() if k not in keep and f <= kept_frames}
    if not drop:
        return masks, []
    hit = sorted(set().union(*(frames[k] for k in drop)))
    for t in hit:
        _, lab = _frame_labels(masks[t], kernel)
        bad = [a for a in range(1, lab.max() + 1) if roots[offs[t] + a] in drop]
        masks[t] &= ~np.isin(lab, bad)
    return masks, hit


class MockEngine:
    name = "mock"

    def segment(self, video, text, start, end, meta, work, log=print, filter_fragments=True):
        n, h, w = meta["n"], meta["h"], meta["w"]
        yy, xx = np.ogrid[:h, :w]
        m = np.zeros((n, h, w), bool)
        for i in range(start, end + 1):
            cx, cy = w * (0.35 + 0.3 * i / max(n - 1, 1)), h * 0.55
            m[i] = ((xx - cx) / (w * 0.1)) ** 2 + ((yy - cy) / (h * 0.32)) ** 2 <= 1
        return [m]

    def segment_points(self, video, frame, pos, neg, start, end, back, meta, work, log=print, tracker="sam"):
        n, h, w = meta["n"], meta["h"], meta["w"]
        yy, xx = np.ogrid[:h, :w]
        m = np.zeros((n, h, w), bool)
        cx, cy = pos[0]["x"], pos[0]["y"]
        for i in range(start if back else frame, end + 1):
            m[i] = ((xx - cx) / (w * 0.08)) ** 2 + ((yy - cy) / (h * 0.25)) ** 2 <= 1
        return m, (m.astype(np.uint8) * 255 if tracker == "matanyone" else None)

    def inpaint_levels(self, p, work, meta, levels, log=print, ref_videos=()):
        """假装生成：重绘区用低分辨率 OpenCV 补洞（测界面流程用）。"""
        check_cancel()
        frames, fps = read_video(p.input_video)
        for i, m in enumerate(p.mask):
            if i % 20 == 0:
                check_cancel()
            if not m.any():
                continue
            small = cv2.resize(frames[i], None, fx=0.25, fy=0.25)
            sm = cv2.resize(m.astype(np.uint8) * 255, small.shape[1::-1], interpolation=cv2.INTER_NEAREST)
            fixed = cv2.resize(cv2.inpaint(small, sm, 5, cv2.INPAINT_TELEA), frames.shape[2:0:-1])
            frames[i][m] = fixed[m]
        out = work / "generated.mp4"
        write_video(out, frames, fps)
        return out


CLEAN_UPLOADS = {"on": True}   # 任务结束后删除上传到 ComfyUI 的素材（设置里改，存 app_settings.json 的 clean_uploads；默认开）


class ComfyUnreachable(RuntimeError):
    """ComfyUI 连不上（重连超时）。"""


class ComfyEngine:
    name = "comfy"

    def __init__(self, url, wf_dir: Path = None):
        self.url = url.rstrip("/")
        self.wf_dir = wf_dir
        self.client = uuid.uuid4().hex
        self.report = None                 # 进度回调：server 设成写进任务状态，界面显示进度条
        self.loras = []                    # H3 用的 LoRA：[{name, strength}]，界面上选，存在 app_settings.json
        self.unet = self.FILE_SLOTS["h3.unet"]["default"]   # H3 主模型（UNETLoader 的文件名），界面上选，存在 app_settings.json
        self._ws_state = {}                # prompt_id → {node, value, max, t0, v0, updated}
        self._ws_thread = None
        self._ws_ok = False
        self.http = requests.Session()
        self.http.trust_env = False          # 不走系统代理（局域网地址经代理会连不上）
        self.uploaded = {}                   # 上传到 ComfyUI 的文件名 → 本地文件（写 inputused.txt 用）
        self._nodes_seen = set()             # has_node 查到装了的节点

    def _req(self, method, url, **kw):
        """网络请求：断线、超时、ComfyUI 重启（5xx）时重连，等待 5 秒起、逐步加到 60 秒封顶。

        kw["give_up"] 是这次请求最多重连多少秒（默认 180）：连不上就抛错，别把服务端的线程一直占着——
        否则 ComfyUI 一关，界面所有请求都会排队卡死（用户看到的就是「请求超时」）。
        """
        give_up = kw.pop("give_up", 180)
        wait, t0 = 5, time.time()
        while True:
            try:
                r = self.http.request(method, url, **kw)
                if r.status_code < 500:
                    if time.time() - t0 > 10:
                        print(f"  [网络] 已重新连上（断开 {int(time.time() - t0)} 秒）", flush=True)
                    return r
                err = f"HTTP {r.status_code}"
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                    requests.exceptions.ChunkedEncodingError) as e:
                err = type(e).__name__
            if time.time() - t0 > give_up:
                raise ComfyUnreachable(f"{err}：连不上 ComfyUI（{self.url}），已经等了 {int(time.time() - t0)} 秒")
            print(f"  [网络] {err}，{wait} 秒后重连（已断开 {int(time.time() - t0)} 秒）", flush=True)
            time.sleep(wait)
            wait = min(wait * 2, 60)

    # 上传到 ComfyUI input 下的子文件夹：LoadImage / LoadVideo 等节点每次列可选项都要扫一遍 input 根目录，
    # 文件堆在根目录会让 /object_info（打开 ComfyUI 网页就要调）慢到几十秒、网页卡在 logo。
    UPLOAD_SUB = "reframe"

    def _upload(self, path: Path):
        data = Path(path).read_bytes()                    # 先读进内存，重连时可以重复发送
        name = f"videops_{uuid.uuid4().hex[:8]}_{Path(path).name}"     # 唯一文件名
        r = self._req("POST", f"{self.url}/upload/image", files={"image": (name, data)},
                      data={"overwrite": "true", "subfolder": self.UPLOAD_SUB}, timeout=600)
        r.raise_for_status()
        j = r.json()
        out = f"{j['subfolder']}/{j['name']}" if j.get("subfolder") else j["name"]
        self.uploaded[out] = Path(path).resolve()
        return out

    def _cleanup_uploads(self, graph, log=print):
        """这个工作流用完了：把它用到的、本软件上传的文件从 ComfyUI 的 input 里删掉（要 ComfyUI-Reframe 节点包的删除接口）。
        删不掉（没装新版节点包、连不上）只记一行，不影响结果。"""
        names = sorted({v for n in graph.values() for v in n.get("inputs", {}).values()
                        if isinstance(v, str) and v in self.uploaded})
        if not names:
            return
        for n in names:
            self.uploaded.pop(n, None)
        if not CLEAN_UPLOADS["on"] or getattr(self, "_no_cleanup", False):
            return
        try:
            r = self._req("POST", f"{self.url}/reframe/input/delete", json={"names": names}, timeout=20, give_up=10)
            if r.status_code in (404, 405):            # 没有这个路由（ComfyUI 对未知 POST 回 405）
                self._no_cleanup = True
                log("  [4090] ComfyUI 上的 ComfyUI-Reframe 节点包是旧版，没有删除接口：上传的文件留在 input/reframe 里。"
                    "把仓库里的 comfyui/ComfyUI-Reframe 覆盖拷到 4090 的 custom_nodes 并重启 ComfyUI 后会自动删。")
            elif not r.ok:
                log(f"  [4090] 删除上传的文件失败：HTTP {r.status_code} {r.text[:200]}")
        except Exception as e:
            log(f"  [4090] 删除上传的文件失败：{e}")

    def write_inputs_used(self, work, graph, p, extra=()):
        """把这次 H3 真正用到的全部材料写进 work/inputused.txt：直接从发给 ComfyUI 的工作流里读，和实际传的一模一样。"""
        g = lambda nid: graph.get(str(nid), {}).get("inputs", {})
        local = lambda name: str(self.uploaded.get(name, f"（ComfyUI 上的 {name}）"))
        labels = list(getattr(p, "ref_labels", None) or [])
        r2v = g(12)
        lines = [f"生成：{p.label}", f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", f"ComfyUI：{self.url}", "",
                 "【模型】",
                 f"  H3 主模型：{g(1).get('unet_name')}",
                 f"  文本编码：{g(2).get('clip_name')}",
                 f"  视频 VAE：{g(3).get('vae_name')}　音频 VAE：{g(4).get('vae_name')}"]
        loras = [(v["lora_name"], v["strength_model"]) for k, v in sorted(((k, x["inputs"]) for k, x in graph.items()
                                                                           if x["class_type"] == "LoraLoaderModelOnly"), key=lambda t: int(t[0]))]
        lines += [f"  LoRA：{n} @ {s}" for n, s in loras] or ["  LoRA：无"]
        smp = g(18)
        lines += ["", "【采样】",
                  f"  采样器 {g(17).get('sampler_name')}，{smp.get('steps')} 步，scheduler {smp.get('scheduler')}，denoise {smp.get('denoise')}",
                  f"  seed {g(15).get('noise_seed')}",
                  f"  画布 {r2v.get('width')}x{r2v.get('height')}，{r2v.get('length')} 帧" if not isinstance(r2v.get('width'), list)
                  else f"  画布：h3_input 整幅，{r2v.get('length')} 帧",
                  "", "【加噪的底（要重绘的视频）】",
                  f"  {local(g(5).get('file'))}",
                  "", "【重绘遮罩】",
                  f"  {local(g(7).get('file'))}",
                  ("  （像素级、无损；ComfyUI 里 NKDMaskOps（" + "，".join(f"{k}={v}" for k, v in g(26).items()
                                                               if k not in ("mask", "vae", "model"))
                   + "）转 latent 网格 → NKDAVLatent（audio_mode=" + str(g(27).get("audio_mode")) + "，粗剪画面 + 原片音频）"
                   + "；black_point = white_point = 0.5 会先二值化，0.95 / 0.98 也按完全重绘）") if "26" in graph else
                  ("  （像素级、无损；> 0.5 = 重绘；ComfyUI 里 ReframeH3InpaintMask 按 H3 的 32px 格 / 时间组转潜空间，"
                   f"本体 mask 另传，同一格冲突时 conflict={g(42).get('conflict')}）")
                  if g(42).get("conflict") else
                  "  （像素级、无损；" + ("每个像素的去噪强度 0~255，不二值化" if "24" not in graph else "二值化：白 = 重绘")
                  + (f"；ComfyUI 里 {graph['25']['class_type']}（{g(25).get('compression')}，空间 {g(25).get('spatial_method')}、"
                     f"时间 {g(25).get('temporal_method')}）按 H3 VAE 转潜空间" if "25" in graph else "") + "）"]
        pics = [g(30 + k).get("image") for k in range(9) if f"ref_images.ref_image_{k}" in r2v]
        lines += ["", f"【参考图 <Picture N>】{len(pics)} 张"]
        for k, name in enumerate(pics):
            lines.append(f"  <Picture {k + 1}>" + (f"（{labels[k]}）" if k < len(labels) and labels[k] else "") + f"：{local(name)}")
        vids = [g(50 + k).get("file") for k in range(3) if f"ref_videos.ref_video_{k}" in r2v]
        lines += ["", f"【参考视频 <Video N>】{len(vids)} 段"]
        lines += [f"  <Video {k + 1}>：{local(name)}" for k, name in enumerate(vids)]
        if "28" in graph:
            lines += ["", "【aligned guide】",
                      f"  加噪的底那段视频（{local(g(5).get('file'))}）整段经 MiniMaxH3AddGuide（frame_idx={g(28).get('frame_idx')}）"
                      "逐帧对齐地作为干净条件行"]
        guides = [g(80 + j) for j in range(9) if str(80 + j) in graph]
        if guides:
            lines += ["", "【关键帧钉帧】"]
            for x in guides:
                k = int(x["image"][0]) - 30
                lines.append(f"  <Picture {k + 1}>" + (f"（{labels[k]}）" if k < len(labels) and labels[k] else "")
                             + f" 经 MiniMaxH3AddGuide 钉在第 {x['frame_idx']} 帧")
        if extra:
            lines += ["", "【其他】"] + [f"  {x}" for x in extra]
        lines += ["", "【提示词】", r2v.get("prompt", ""), "", f"完整工作流：{Path(work).resolve() / 'comfy_graph.json'}"]
        (Path(work) / "inputused.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------- 进度：ComfyUI websocket（progress / executing 消息）----------
    def _ensure_ws(self):
        if websocket is None or (self._ws_thread and self._ws_thread.is_alive()):
            return
        self._ws_thread = threading.Thread(target=self._ws_loop, daemon=True)
        self._ws_thread.start()
        for _ in range(30):                                    # 先连上再提交任务，开头几步的进度才不会漏
            if self._ws_ok:
                break
            time.sleep(0.1)

    def _ws_loop(self):
        url = self.url.replace("https://", "wss://").replace("http://", "ws://") + f"/ws?clientId={self.client}"
        u = urlparse(self.url)
        while True:
            try:
                # 不走本机代理（2026-11-18）：websocket-client 会读 HTTP_PROXY 环境变量（http_no_proxy 参数实测不管用），
                # 经代理的长连接会僵住——不报错、也收不到消息（二采那次整整 10 分钟只显示「等待进度」）。
                # 自己直连好 TCP 再交给它；https 的 ComfyUI 少见，照旧让库自己连。
                kw = {}
                if u.scheme == "http":
                    kw["socket"] = socket.create_connection((u.hostname, u.port or 80), timeout=15)
                ws = websocket.create_connection(url, timeout=15, **kw)
                ws.settimeout(30)
                self._ws_ok = True
                while True:
                    try:
                        msg = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        ws.ping()                              # 30 秒没消息（采样一步可能好几分钟）：发心跳，连接死了这里会报错 → 重连
                        continue
                    if isinstance(msg, (bytes, bytearray)):     # 采样预览图，不用
                        continue
                    self._on_ws(json.loads(msg))
            except Exception:
                self._ws_ok = False
                time.sleep(5)                                  # 断了就重连，和 HTTP 一样

    def _on_ws(self, m):
        t, d = m.get("type"), m.get("data") or {}
        pid = d.get("prompt_id")
        if not pid:
            return
        st = self._ws_state.setdefault(pid, dict(node=None, value=0, max=0, t0=None, v0=0, started=None))
        now = time.time()
        if t == "execution_start":
            st["started"] = now
        elif t == "executing":
            if d.get("node") is None:                  # 整个工作流跑完了（马上去取结果）：别退回“等待进度”
                st.update(node="__done__", value=0, max=0)
            else:
                st.update(node=d.get("node"), value=0, max=0, t0=None, started=st["started"] or now)
        elif t == "progress":
            if d.get("node") and d["node"] != st["node"]:
                st.update(node=d["node"], t0=None)
            v, mx = int(d.get("value", 0)), int(d.get("max", 0))
            if st["t0"] is None or v < st["value"]:
                st["t0"], st["v0"] = now, v                    # 这一段计时的起点（算剩余时间用）
            st.update(value=v, max=mx, started=st["started"] or now)
        elif t == "progress_state":                            # 新版 ComfyUI：一次给出所有在跑的节点
            run = [n for n in (d.get("nodes") or {}).values() if n.get("state") == "running"]
            if run:
                n = max(run, key=lambda n: n.get("max", 0))
                self._on_ws(dict(type="progress", data=dict(prompt_id=pid, node=n.get("node_id") or n.get("display_node_id"),
                                                           value=n.get("value", 0), max=n.get("max", 0))))

    def _queue_ahead(self, pid):
        """排在这个任务前面的数量（4090 上别人 / 别的程序的任务也算）；已经在跑返回 0。"""
        try:
            q = self._req("GET", f"{self.url}/queue", timeout=20, give_up=10).json()
        except Exception:
            return None
        if any(it[1] == pid for it in q.get("queue_running", [])):
            return 0
        pend = sorted(q.get("queue_pending", []), key=lambda it: it[0])
        ids = [it[1] for it in pend]
        return len(q.get("queue_running", [])) + (ids.index(pid) if pid in ids else len(ids))

    def _in_queue(self, pid):
        try:
            q = self._req("GET", f"{self.url}/queue", timeout=20, give_up=10).json()
        except Exception:
            return True                                # 查不到就当它还在，别误报
        return any(it[1] == pid for it in q.get("queue_running", []) + q.get("queue_pending", []))

    def _status(self, pid, graph, t0):
        """当前阶段 → (一行文字, 进度 dict)。"""
        st = self._ws_state.get(pid)
        el = time.time() - t0
        if not st or not st.get("node"):
            ahead = self._queue_ahead(pid)
            if ahead:
                return f"排队中：前面还有 {ahead} 个任务（4090 上的其他任务），已等 {fmt_secs(el)}", \
                    dict(stage="排队中", queue=ahead, elapsed=el)
            text = "已开始，等待进度…" if self._ws_ok else "运行中（没连上 ComfyUI 的进度通道，只能显示用时）"
            return f"{text} {fmt_secs(el)}", dict(stage="运行中", elapsed=el)
        if st["node"] == "__done__":
            return f"跑完了，取结果（已用 {fmt_secs(el)}）", dict(stage="取结果", elapsed=el)
        cls = (graph.get(str(st["node"])) or {}).get("class_type", "")
        stage = STAGES.get(cls, "运行中")
        info = dict(stage=stage, elapsed=el)
        text = stage
        if st["max"] > 1:
            info.update(value=st["value"], max=st["max"])
            text += f" {st['value']}/{st['max']}"
            done = st["value"] - st["v0"]
            if st["t0"] and done > 0:
                eta = (time.time() - st["t0"]) / done * (st["max"] - st["value"])
                info["eta"] = eta
                text += f"，这一步还剩约 {fmt_secs(eta)}"
        return f"{text}（已用 {fmt_secs(el)}）", info

    def _submit(self, graph):
        """提交工作流。prompt_id 自己定：提交时断线 / 超时（ComfyUI 可能已经收下了），先查它在不在队列 / 历史里再决定重交，
        不会把同一个 H3 任务排两遍（排两遍要多跑 8–10 分钟，停止也只停得掉一个）。"""
        pid = str(uuid.uuid4())                        # 和 ComfyUI 自己生成的格式一样
        body = {"prompt": graph, "client_id": self.client, "prompt_id": pid}
        wait, t0 = 5, time.time()
        while True:
            try:
                r = self.http.post(f"{self.url}/prompt", json=body, timeout=60)
                if r.status_code < 500:
                    break
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                    requests.exceptions.ChunkedEncodingError):
                pass
            try:                                       # 可能已经交上去了：在队列 / 历史里就直接用
                if self._in_queue_strict(pid) or pid in self._req("GET", f"{self.url}/history/{pid}", timeout=20, give_up=10).json():
                    return pid
            except Exception:
                pass
            if time.time() - t0 > 180:
                raise ComfyUnreachable(f"提交工作流失败：连不上 ComfyUI（{self.url}），已经等了 {int(time.time() - t0)} 秒")
            time.sleep(wait)
            wait = min(wait * 2, 60)
        if r.status_code != 200:
            raise RuntimeError(f"ComfyUI 拒绝了工作流：{r.text[:3000]}")
        return r.json().get("prompt_id") or pid          # 旧版 ComfyUI 不认自定的 prompt_id：用它回的

    def _in_queue_strict(self, pid):
        """和 _in_queue 一样，但查不到就算不在（提交时判断要不要重交用）。"""
        q = self._req("GET", f"{self.url}/queue", timeout=20, give_up=10).json()
        return any(it[1] == pid for it in q.get("queue_running", []) + q.get("queue_pending", []))

    def _stop_remote(self, pid):
        """让 4090 停下这个任务：执行中的打断、排队中的删掉。ComfyUI 连不上时最多等 10 秒，不卡住停止。"""
        for path, body in (("/interrupt", None), ("/queue", {"delete": [pid]})):
            try:
                self._req("POST", f"{self.url}{path}", json=body, timeout=10, give_up=10)
            except Exception:
                pass

    def _run(self, graph, log=print, timeout=7200):
        self._ensure_ws()
        pid = None
        t0, last_log, last_hist, last_stage = time.time(), 0, 0, None
        missing = 0                                    # 连续几次既不在队列里、也不在历史里 = ComfyUI 崩溃重启，任务丢了
        try:
            self._free_on_switch(graph, log)           # 视频生成 ↔ 其他任务切换时先卸载模型（同一种任务连着跑不卸载）
            pid = self._submit(graph)                  # 放进 try：ComfyUI 拒收工作流时也要删掉已上传的文件
            while time.time() - t0 < timeout:
                now = time.time()
                if CANCEL.is_set():                    # 用户点了停止：先让 4090 停下（执行中的打断、排队中的删掉）
                    self._stop_remote(pid)
                    log("  [4090] 已停止这个任务")
                    raise Cancelled("已停止")
                if now - last_hist >= 2:
                    # 断网期间 ComfyUI 那边的任务还在跑：这里只是等着重连，然后继续查同一个任务
                    last_hist = now
                    h = self._req("GET", f"{self.url}/history/{pid}", timeout=60).json()
                    if pid in h:
                        st = h[pid].get("status", {})
                        if st.get("status_str") == "error":
                            msgs = [m for m in st.get("messages", []) if m[0] == "execution_error"]
                            raise RuntimeError("ComfyUI 执行出错：" + json.dumps(msgs or st.get("messages", []), ensure_ascii=False)[:3000])
                        log(f"  [4090] 完成，用时 {fmt_secs(time.time() - t0)}")
                        return h[pid]["outputs"]
                    if not self._in_queue(pid):
                        missing += 1
                        if missing >= 3:
                            raise RuntimeError("ComfyUI 中途崩溃了（或被重启），这个任务已经丢了。请看 4090 上 ComfyUI 的窗口，"
                                               "等它重新启动后再点一次。常见原因：显存不够（4090 上同时在跑别的任务）。")
                    else:
                        missing = 0
                text, info = self._status(pid, graph, t0)
                if self.report:
                    self.report(info)
                if info["stage"] != last_stage or now - last_log > 30:   # 换阶段立刻写一行，同一阶段每 30 秒一行
                    log(f"  [4090] {text}")
                    last_stage, last_log = info["stage"], now
                time.sleep(1)
            self._stop_remote(pid)                     # 等太久：4090 上的也停掉，别让它接着白跑
            raise TimeoutError("ComfyUI 超时")
        finally:
            self._ws_state.pop(pid, None)
            if self.report:
                self.report(None)
            self._cleanup_uploads(graph, log)              # 成功、出错、停止都删：每次用到才传，用完就删

    # ---------- 显存：视频生成和别的任务之间切换时才卸载模型（2026-11-17 用户定） ----------
    # H3 视频生成极吃显存 / 内存：之前留着的 Qwen Image、SAM、看图模型、PE 会把 4090 挤崩（ComfyUI 进程直接退出）。
    # 连续出几张首帧 / 背景图、连续识别这类同一种任务不卸载（模型留着，下一张快）；只在「视频 ↔ 其他」切换时 POST /free。
    FREE_WAIT = 3       # /free 只是给 ComfyUI 的任务循环插个标记：等它处理完再交下一个任务，免得新任务先跑、卸载在后

    @classmethod
    def job_kind(cls, graph):
        """这个工作流是 H3 视频生成（"video"）还是别的（"other"：改图、背景、识别、抠图、看图、PE…）。"""
        video = (cls.H3_MASK_NODE, "ReframeVideoInput")             # 自己的视频工作流也算视频生成（2026-11-18）
        return "video" if any(n.get("class_type") in video for n in graph.values() if isinstance(n, dict)) else "other"

    def _free_on_switch(self, graph, log):
        """视频 ↔ 其他切换时让 ComfyUI 卸载所有模型、清缓存。软件刚启动时不知道 ComfyUI 里留着什么：视频生成先卸载，其他不管。
        失败只打日志，不影响任务。"""
        kind, prev = self.job_kind(graph), getattr(self, "_last_kind", None)
        self._last_kind = kind
        if kind == prev or (prev is None and kind != "video"):
            return
        try:
            r = self._req("POST", f"{self.url}/free", json={"unload_models": True, "free_memory": True}, timeout=30, give_up=20)
            if not r.ok:
                return log(f"  让 ComfyUI 释放显存没成功（HTTP {r.status_code}），照常继续")
            log("  [4090] 换成视频生成：先卸载之前的模型、释放显存和内存" if kind == "video"
                else "  [4090] 视频生成之后换别的任务：先卸载 H3、释放显存和内存")
            time.sleep(self.FREE_WAIT)
        except Exception as e:
            log(f"  让 ComfyUI 释放显存没成功（{e}），照常继续")

    def has_node(self, name):
        """4090 的 ComfyUI 装没装这个节点（/object_info/<name> 不认识的节点返回 {}）。只缓存“装了”：装上新节点重启 ComfyUI 后不用重启软件。"""
        if name in self._nodes_seen:
            return True
        r = self._req("GET", f"{self.url}/object_info/{name}", timeout=30, give_up=20)
        ok = r.ok and name in r.json()
        if ok:
            self._nodes_seen.add(name)
        return ok

    def node_has_input(self, name, inp):
        """ComfyUI 上这个节点有没有某个输入（旧版节点包没有新加的输入时，提示用户更新）。只缓存“有”。"""
        key = f"{name}.{inp}"
        if key in self._nodes_seen:
            return True
        r = self._req("GET", f"{self.url}/object_info/{name}", timeout=30, give_up=20)
        spec = (r.json().get(name) or {}).get("input", {}) if r.ok else {}
        ok = any(inp in (spec.get(k) or {}) for k in ("required", "optional"))
        if ok:
            self._nodes_seen.add(key)
        return ok

    # ---------- H3 LoRA ----------
    def list_loras(self):
        """ComfyUI loras 文件夹里的全部 LoRA（LoraLoaderModelOnly 的可选项）。"""
        r = self._req("GET", f"{self.url}/object_info/LoraLoaderModelOnly", timeout=30, give_up=20)
        r.raise_for_status()
        return list(r.json()["LoraLoaderModelOnly"]["input"]["required"]["lora_name"][0])

    def list_unets(self):
        """ComfyUI diffusion_models 文件夹里的 H3 模型（UNETLoader 的可选项里名字带 h3 的；别的模型接不上 H3 的工作流）。"""
        r = self._req("GET", f"{self.url}/object_info/UNETLoader", timeout=30, give_up=20)
        r.raise_for_status()
        return [n for n in r.json()["UNETLoader"]["input"]["required"]["unet_name"][0] if "h3" in n.lower()]

    def list_sams(self):
        """识别模型：ComfyUI checkpoints 文件夹里名字带 sam 的（CheckpointLoaderSimple 的可选项；识别图用 SAM3_VideoTrack / SAM3_Detect）。"""
        r = self._req("GET", f"{self.url}/object_info/CheckpointLoaderSimple", timeout=30, give_up=20)
        r.raise_for_status()
        return [n for n in r.json()["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0] if "sam" in n.lower()]

    def _apply_loras(self, node, model_id):
        """在 H3 模型后面串上选好的 LoRA，返回最后一个节点的 id（采样器用它的输出）。"""
        for k, l in enumerate(self.loras):
            model_id = node(90 + k, "LoraLoaderModelOnly", model=[model_id, 0], lora_name=l["name"],
                            strength_model=float(l.get("strength", 1.0)))
        return model_id

    def _download(self, outputs, node, dest: Path):
        files = [f for v in outputs.get(node, {}).values() if isinstance(v, list) for f in v
                 if isinstance(f, dict) and "filename" in f]
        if not files:
            raise RuntimeError(f"输出节点 {node} 没有文件，outputs = {json.dumps(outputs)[:500]}")
        paths = []
        for k, f in enumerate(files):
            r = self._req("GET", f"{self.url}/view", params=dict(filename=f["filename"], subfolder=f.get("subfolder", ""),
                                                                  type=f.get("type", "output")), timeout=600)
            r.raise_for_status()
            p = dest / f"out_{k}{Path(f['filename']).suffix}"
            p.write_bytes(r.content)
            paths.append(p)
        return paths

    # ---------- 分割 ----------
    SAM_CKPT = "sam3.1_multiplex_fp16.safetensors"
    SAM_MAX_OBJECTS = 6

    def build_segment_graph(self, video_name, text, start, end, threshold=0.5):
        g = {}

        def node(nid, cls, **inputs):
            g[str(nid)] = {"class_type": cls, "inputs": inputs}
            return str(nid)

        ck = node(1, "CheckpointLoaderSimple", ckpt_name=self.SAM_CKPT)
        cond = node(2, "CLIPTextEncode", text=text, clip=[ck, 1])
        src = node(4, "GetVideoComponents", video=[node(3, "LoadVideo", file=video_name), 0])
        clip = node(5, "ImageFromBatch", image=[src, 0], batch_index=start, length=end - start + 1)   # 只在时间范围内跟踪
        track = node(6, "SAM3_VideoTrack", images=[clip, 0], model=[ck, 0], conditioning=[cond, 0],
                     detection_threshold=threshold, max_objects=self.SAM_MAX_OBJECTS, detect_interval=1)
        outs = []
        for k in range(self.SAM_MAX_OBJECTS):                # 每个物体单独导出一个遮罩视频，空的由软件丢掉
            m = node(10 + k, "SAM3_TrackToMask", track_data=[track, 0], object_indices=str(k))
            v = node(30 + k, "CreateVideo", images=[node(20 + k, "MaskToImage", mask=[m, 0]), 0], fps=[src, 2])
            outs.append(node(40 + k, "SaveVideo", video=[v, 0], filename_prefix=f"videops/seg_{k}",
                             format="auto", codec="auto"))
        return g, outs

    # ---------- 点选识别：SAM 3.1 在一帧上按点出遮罩（SAM3_Detect 的 positive / negative_coords），再从这一帧跟踪（SAM3_VideoTrack 的 initial_mask）----------
    def build_point_graph(self, video_name, first, length, pos, neg):
        """视频第 first 帧起 length 帧：第 0 帧按点出遮罩，整段跟踪这一个物体。点 = 原视频像素坐标 [{"x", "y"}]。"""
        g = {}

        def node(nid, cls, **inputs):
            g[str(nid)] = {"class_type": cls, "inputs": inputs}
            return str(nid)

        ck = node(1, "CheckpointLoaderSimple", ckpt_name=self.SAM_CKPT)
        src = node(4, "GetVideoComponents", video=[node(3, "LoadVideo", file=video_name), 0])
        clip = node(5, "ImageFromBatch", image=[src, 0], batch_index=first, length=length)
        f0 = node(6, "ImageFromBatch", image=[clip, 0], batch_index=0, length=1)
        pts = dict(positive_coords=json.dumps([{"x": int(p["x"]), "y": int(p["y"])} for p in pos]))
        if neg:
            pts["negative_coords"] = json.dumps([{"x": int(p["x"]), "y": int(p["y"])} for p in neg])
        det = node(7, "SAM3_Detect", model=[ck, 0], image=[f0, 0], threshold=0.5, refine_iterations=2, individual_masks=False, **pts)
        track = node(8, "SAM3_VideoTrack", images=[clip, 0], model=[ck, 0], initial_mask=[det, 0],
                     detection_threshold=0.5, max_objects=1, detect_interval=1)
        m = node(10, "SAM3_TrackToMask", track_data=[track, 0], object_indices="0")
        v = node(30, "CreateVideo", images=[node(20, "MaskToImage", mask=[m, 0]), 0], fps=[src, 2])
        out = node(40, "SaveVideo", video=[v, 0], filename_prefix="videops/pick", format="auto", codec="auto")
        return g, out

    def detect_points(self, video, frame, pos, neg, meta, work, log=print):
        """只在第 frame 帧按点出遮罩（SAM3_Detect），返回 (h,w) bool。MatAnyone2 跟踪要的首帧遮罩。"""
        g = {}

        def node(nid, cls, **inputs):
            g[str(nid)] = {"class_type": cls, "inputs": inputs}
            return str(nid)

        ck = node(1, "CheckpointLoaderSimple", ckpt_name=self.SAM_CKPT)
        src = node(4, "GetVideoComponents", video=[node(3, "LoadVideo", file=self._upload(Path(video))), 0])
        f0 = node(5, "ImageFromBatch", image=[src, 0], batch_index=frame, length=1)
        pts = dict(positive_coords=json.dumps([{"x": int(p["x"]), "y": int(p["y"])} for p in pos]))
        if neg:
            pts["negative_coords"] = json.dumps([{"x": int(p["x"]), "y": int(p["y"])} for p in neg])
        det = node(7, "SAM3_Detect", model=[ck, 0], image=[f0, 0], threshold=0.5, refine_iterations=2, individual_masks=False, **pts)
        out = node(9, "SaveImage", images=[node(8, "MaskToImage", mask=[det, 0]), 0], filename_prefix="videops/pickmask")
        (work / "comfy_graph_detect.json").write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
        res = self._run(g, log)
        p = self._download(res, out, work)[0]
        a = np.array(Image.open(p).convert("L"))
        h, w = meta["h"], meta["w"]
        return (a if a.shape == (h, w) else cv2.resize(a, (w, h), interpolation=cv2.INTER_NEAREST)) > 127

    def _reverse_clip(self, video, start, frame, work):
        rev = work / "pick_back_src.mp4"
        r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(video), "-an",
                            "-vf", f"select='between(n\\,{start}\\,{frame})',reverse,setpts=N/FRAME_RATE/TB",
                            "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(rev)], capture_output=True)
        if r.returncode:
            raise RuntimeError("倒放片段没做出来：" + r.stderr.decode("utf-8", "replace")[-300:])
        return rev

    def _track_points(self, video, first, length, pos, neg, work, tag, log):
        graph, out = self.build_point_graph(self._upload(Path(video)), first, length, pos, neg)
        (work / f"comfy_graph_{tag}.json").write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
        result = self._run(graph, log)
        p = self._download(result, out, work)[0].replace(work / f"pick_{tag}.mp4")
        frames, _ = read_video(p)
        return frames[..., 0] > 127

    def segment_points(self, video, frame, pos, neg, start, end, back, meta, work, log=print, tracker="sam"):
        """点选识别：第 frame 帧按点出遮罩，往后跟踪到 end；back = 往前也跟踪到 start（把 start..frame 倒过来的片段
        在笔记本上用 ffmpeg 做好上传，再跑一次，结果倒回去）。
        tracker = "sam"：SAM3_VideoTrack 跟踪（硬边），返回 (遮罩, None)；
        "matanyone"：SAM3_Detect 只出这一帧遮罩，MatAnyone2 跟踪全段（柔边），返回 (alpha ≥ 128, alpha)。"""
        n, h, w = meta["n"], meta["h"], meta["w"]
        if tracker == "matanyone":
            log(f"点选识别（MatAnyone2 跟踪）：第 {frame} 帧，{len(pos)} 个选中点、{len(neg)} 个排除点；往后到第 {end} 帧")
            m0 = self.detect_points(video, frame, pos, neg, meta, work, log)
            if not m0.any():
                return np.zeros((n, h, w), bool), None
            first = np.zeros((n, h, w), bool)
            first[frame] = m0
            alpha = self.matte(video, first, frame, end, meta, work, log)
            if back and frame > start:
                check_cancel()
                log(f"往前跟踪：第 {frame}–{start} 帧（倒放）")
                k = frame - start + 1
                rev = self._reverse_clip(video, start, frame, work)
                fr = np.zeros((k, h, w), bool)
                fr[0] = m0
                ab = self.matte(rev, fr, 0, k - 1, dict(meta, n=k), work, log)
                for j in range(1, k):
                    alpha[frame - j] = ab[j]
            return alpha >= 128, alpha
        full = np.zeros((n, h, w), bool)
        put = lambda i, a: full.__setitem__(i, cv2.resize(a.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0)
        log(f"点选识别：第 {frame} 帧，{len(pos)} 个选中点、{len(neg)} 个排除点；往后跟踪到第 {end} 帧")
        m = self._track_points(video, frame, end - frame + 1, pos, neg, work, "fwd", log)
        for k in range(min(len(m), end - frame + 1)):
            put(frame + k, m[k])
        if back and frame > start:
            check_cancel()
            log(f"往前跟踪：第 {frame}–{start} 帧（倒放）")
            rev = self._reverse_clip(video, start, frame, work)
            mb = self._track_points(rev, 0, frame - start + 1, pos, neg, work, "back", log)
            for k in range(1, min(len(mb), frame - start + 1)):   # 第 0 个就是 frame 本身，往后那次已经有了
                put(frame - k, mb[k])
        return full, None

    def segment(self, video, text, start, end, meta, work, log=print, filter_fragments=True):
        graph, outs = self.build_segment_graph(self._upload(Path(video)), text, start, end)
        (work / "comfy_graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
        result = self._run(graph, log)
        n, h, w = meta["n"], meta["h"], meta["w"]
        masks = []
        for k, node_id in enumerate(outs):
            p = self._download(result, node_id, work)[0].replace(work / f"seg_{k}.mp4")
            frames, _ = read_video(p)
            m = frames[..., 0] > 127
            if not m.any():
                continue
            full = np.zeros((n, h, w), bool)
            for i in range(min(len(m), n - start)):          # 跟踪结果从 start 帧开始
                full[start + i] = cv2.resize(m[i].astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
            full, hit = drop_flicker_fragments(full) if filter_fragments else (full, [])
            if hit:
                log(f"  物体 {k}：删掉只闪几帧的碎块，第 {', '.join(map(str, hit))} 帧")
            masks.append(full)
        return masks

    # ---------- MatAnyone2 抠像：SAM 硬遮罩 → 半透明 alpha（头发丝、运动模糊）----------
    def matte(self, video, mask, start, end, meta, work, log=print):
        """mask: (n,h,w) bool，取 start 帧当作首帧遮罩。返回 (n,h,w) uint8 alpha，范围外为 0。"""
        from PIL import Image
        m0 = work / f"matte_mask_{start}.png"
        Image.fromarray(mask[start].astype(np.uint8) * 255).save(m0)
        g = {
            "1": {"class_type": "LoadVideo", "inputs": {"file": self._upload(Path(video))}},
            "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
            "3": {"class_type": "ImageFromBatch", "inputs": {"image": ["2", 0], "batch_index": start,
                                                             "length": end - start + 1}},
            "4": {"class_type": "LoadImage", "inputs": {"image": self._upload(m0)}},
            "5": {"class_type": "ImageToMask", "inputs": {"image": ["4", 0], "channel": "red"}},
            "6": {"class_type": "MatAnyone2", "inputs": {"src_video": ["3", 0], "mask_frame": 0, "n_warmup": 10,
                                                         "foreground_MASK": ["5", 0], "r_erode": 0, "r_dilate": 0,
                                                         "max_internal_size": -1, "max_mem_frames": 5,
                                                         "use_long_term": False}},
            "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "videops/matte"}},
        }
        res = self._run(g, log)
        files = [f for v in res.get("7", {}).values() if isinstance(v, list) for f in v if isinstance(f, dict)]
        files.sort(key=lambda f: f["filename"])           # SaveImage 按 _00001_ 编号，排序保证帧序
        paths = self._download({"7": {"images": files}}, "7", work)
        n, h, w = meta["n"], meta["h"], meta["w"]
        alpha = np.zeros((n, h, w), np.uint8)
        for k, p in enumerate(paths[: n - start]):
            a = np.array(Image.open(p).convert("L"))
            alpha[start + k] = a if a.shape == (h, w) else cv2.resize(a, (w, h), interpolation=cv2.INTER_LINEAR)
            p.unlink()
        return alpha

    # ---------- Qwen Image 2.1 图像编辑（关键帧去除物体等）----------
    # 可选的 Qwen Image 2.1 主模型（界面右上角选择）；steps = 这个模型默认的采样步数
    # pe = 选这个模型时 PE 下拉自动切到哪个（用户可以再手动换）；node = 这个模型要 ComfyUI 有的节点（没有就灰掉）
    QWEN_MODELS = {
        "turbo": dict(label="Qwen 2.1 Turbo（8 步，快）", unet="qwen_image_2.1_turbo_bf16.safetensors", slot="qwen.turbo", steps=8, pe="qwen"),
        "full": dict(label="Qwen 2.1 完整版（25 步）", unet="qwen_image_2.1_bf16.safetensors", slot="qwen.full", steps=25, pe="qwen"),
        "int8": dict(label="Qwen 2.1 int8（25 步）", unet="qwen_image_2.1_int8_convrot.safetensors", slot="qwen.int8", steps=25, pe="qwen"),
        "h3img": dict(label="MiniMax H3 Image（6 步）", unet="minimax_h3_image_pruned_int8_convrot.safetensors", slot="h3img.unet", steps=6, pe="h3",
                      family="h3", node="MiniMaxH3ImageConditioning"),
    }
    qwen_model = "turbo"

    # ---------- MiniMax-H3-Image（照官方模板 image_minimax_h3_image_edit：ComfyUI 0.45.0 起的原生节点）----------
    # 没有遮罩输入：整张交给模型，「只重画 / 局部重画」照旧由调用方按遮罩贴回。第 1 张先放大到约 4 MP（官方：低分辨率效果差）。
    H3IMG_CLIP = "qwen3vl_32b_minimax_h3_int8_convrot.safetensors"    # 和 H3 视频同一个文本编码器
    H3IMG_VAE = "minimax_h3_image_vae_fp16.safetensors"
    H3IMG_PIXELS = 4.0e6
    H3IMG_MAX_PICS = 16                   # 官方文档：最多 16 张图（<Picture 1>…<Picture 16>）

    # ---------- PE（提示词改写）：和改图模型分开选 ----------
    PE_MODELS = {
        "qwen": dict(label="Qwen 2.1 PE", clip="qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors", slot="pe.qwen", node="TextGenerate"),
        "h3": dict(label="H3 Image PE", clip="qwen3.5_9b_w4a8_gptq.safetensors", slot="pe.h3", node="TextGenerateV2"),
    }
    pe_model = "qwen"

    # ---------- 模型文件槽位（2026-11-18）：同一个模型有很多量化版（bf16 / fp8 / int8 / nvfp4…），文件名不同、加载节点一样 ----------
    # 每个槽位 = 工作流里用到的一个模型文件：默认文件名 + 用哪个加载节点的列表。用户在「模型文件」页选的存 app_settings.json 的
    # model_files（self.files）。没选、默认文件又不在 ComfyUI 上时，自动用同一模型的另一个量化版（same_model）。
    # h3.unet（H3 视频主模型）特殊：选的存在 self.unet / 设置 h3_model（和「视频生成 → 模型」下拉同一个）。SAM 在面板上选。
    # GGUF（要别的加载节点）先不做。
    FILE_SLOTS = {
        "h3.unet": dict(label="H3 主模型", loader=("UNETLoader", "unet_name"), default="minimax_h3_ref2va_int8_convrot.safetensors"),
        "h3.clip": dict(label="H3 文本编码器", loader=("CLIPLoader", "clip_name"), default="qwen3vl_32b_minimax_h3_int8_convrot.safetensors"),
        "h3.vae": dict(label="H3 视频 VAE", loader=("VAELoader", "vae_name"), default="minimax_h3_video_vae_fp16.safetensors"),
        "h3.audio_vae": dict(label="H3 音频 VAE", loader=("VAELoader", "vae_name"), default="minimax_h3_audio_vae_fp32.safetensors"),
        "qwen.turbo": dict(label="Qwen 2.1 Turbo 主模型", loader=("UNETLoader", "unet_name"), default="qwen_image_2.1_turbo_bf16.safetensors"),
        "qwen.full": dict(label="Qwen 2.1 完整版主模型", loader=("UNETLoader", "unet_name"), default="qwen_image_2.1_bf16.safetensors"),
        "qwen.int8": dict(label="Qwen 2.1 int8 主模型", loader=("UNETLoader", "unet_name"), default="qwen_image_2.1_int8_convrot.safetensors"),
        "qwen.clip": dict(label="Qwen3-VL 8B（Qwen 2.1 文本编码器 / 看图）", loader=("CLIPLoader", "clip_name"),
                          default="qwen3vl_8b_int8_convrot.safetensors"),
        "qwen.vae": dict(label="Qwen 2.1 VAE", loader=("VAELoader", "vae_name"), default="qwen_image_2.1_vae_bf16.safetensors"),
        "h3img.unet": dict(label="H3 Image 主模型", loader=("UNETLoader", "unet_name"), default="minimax_h3_image_pruned_int8_convrot.safetensors"),
        "h3img.clip": dict(label="H3 Image 文本编码器", loader=("CLIPLoader", "clip_name"), default="qwen3vl_32b_minimax_h3_int8_convrot.safetensors"),
        "h3img.vae": dict(label="H3 Image VAE", loader=("VAELoader", "vae_name"), default="minimax_h3_image_vae_fp16.safetensors"),
        "pe.qwen": dict(label="Qwen 2.1 PE（Qwen3.5 9B）", loader=("CLIPLoader", "clip_name"),
                        default="qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors"),
        "pe.h3": dict(label="H3 Image PE（Qwen3.5 9B）", loader=("CLIPLoader", "clip_name"), default="qwen3.5_9b_w4a8_gptq.safetensors"),
    }
    # 「模型文件」页：图片生成 / 视频生成各显示哪几组
    FILE_GROUPS = {
        "image": [("Qwen Image 2.1", ["qwen.turbo", "qwen.full", "qwen.int8", "qwen.clip", "qwen.vae"]),
                  ("MiniMax H3 Image", ["h3img.unet", "h3img.clip", "h3img.vae"]),
                  ("提示词优化（PE）", ["pe.qwen", "pe.h3"])],
        "video": [("MiniMax H3", ["h3.unet", "h3.clip", "h3.vae", "h3.audio_vae"]),
                  ("空区分析（看图模型）", ["qwen.clip", "pe.qwen", "pe.h3"])],
    }
    files = {}                          # 槽位 → 用户选的文件名（server 启动时从设置读进来）
    offline = False                     # 只搭图不联网（导出用的引擎）：不查 ComfyUI 的文件列表

    def _files_of(self, cls, field):
        """这个加载节点在 ComfyUI 上的可选文件；查不到返回 None（不拦）。缓存 30 秒（失败也缓存，免得连不上时每次都等）。"""
        if self.offline:
            return None
        cache = self.__dict__.setdefault("_files_cache", {})
        hit = cache.get((cls, field))
        if hit and time.time() - hit[0] < 30:
            return hit[1]
        try:
            v = self._loader_list(cls, field)
        except Exception:
            v = None
        cache[(cls, field)] = (time.time(), v)
        return v

    def _chosen(self, slot):
        """用户选的文件（没选 = ""）。H3 主模型选的存在 self.unet（面板「模型」下拉）。"""
        if slot == "h3.unet":
            return self.unet if self.unet != self.FILE_SLOTS[slot]["default"] else ""
        return self.files.get(slot) or ""

    def file(self, slot):
        """这个槽位这次用哪个文件：用户选的 > 默认文件 > 默认不在时同一模型的另一个量化版 > 默认（让 ComfyUI 报缺）。"""
        s = self.FILE_SLOTS[slot]
        chosen = self._chosen(slot)
        if chosen:
            return chosen
        have = self._files_of(*s["loader"])
        if have is None or s["default"] in have:
            return s["default"]
        return same_model(s["default"], have) or s["default"]

    def slot_info(self, slot):
        """「模型文件」页的一行：现在用的、默认、是不是自动换的、缺不缺，以及可选文件（同一模型的排前面）。"""
        s = self.FILE_SLOTS[slot]
        have = self._files_of(*s["loader"])
        cur = self.file(slot)
        base = model_base(s["default"])
        opts = sorted(have or [], key=lambda n: (model_base(n) != base, n.lower()))
        chosen = self._chosen(slot)
        return dict(slot=slot, label=s["label"], default=s["default"], current=cur, chosen=chosen,
                    auto=not chosen and cur != s["default"], missing=have is not None and cur not in have,
                    known=have is not None, options=[dict(name=n, same=model_base(n) == base) for n in opts])

    @property
    def QWEN(self):
        m = self.QWEN_MODELS[self.qwen_model]
        return dict(unet=self.file(m["slot"]), steps=m["steps"], clip=self.file("qwen.clip"), vae=self.file("qwen.vae"))

    def _loader_list(self, cls, field):
        info = self.http.get(f"{self.url}/object_info/{cls}", timeout=10).json()
        return set(info[cls]["input"]["required"][field][0])

    def model_missing(self, key):
        """这个改图模型在 ComfyUI 上还缺什么文件（主模型；H3 Image 还要它自己的 VAE 和文本编码器）。查不到时返回 []（不拦）。"""
        m = self.QWEN_MODELS[key]
        slots = [m["slot"]] + (["h3img.vae", "h3img.clip"] if m.get("family") == "h3" else [])
        need = [(*self.FILE_SLOTS[k]["loader"], self.file(k)) for k in slots]
        miss = []
        try:
            for cls, field, name in need:
                if name not in self._loader_list(cls, field):
                    miss.append(name)
        except Exception:
            return []
        return miss

    def qwen_available(self):
        """ComfyUI 上实际有的那几个（主模型、H3 Image 的 VAE / 文本编码器都在；要新节点的还得装了那个节点）。"""
        try:
            have = {c: self._loader_list(c, f) for c, f in (("UNETLoader", "unet_name"), ("VAELoader", "vae_name"), ("CLIPLoader", "clip_name"))}
        except Exception:
            return list(self.QWEN_MODELS)
        ok = lambda m: self.file(m["slot"]) in have["UNETLoader"] and (m.get("family") != "h3" or (
            self.file("h3img.vae") in have["VAELoader"] and self.file("h3img.clip") in have["CLIPLoader"]))
        return [k for k, m in self.QWEN_MODELS.items() if ok(m) and (not m.get("node") or self.has_node(m["node"]))]

    def pe_available(self):
        """ComfyUI 上能跑的 PE（模型文件在 CLIPLoader 列表里，节点也有）。"""
        try:
            info = self.http.get(f"{self.url}/object_info/CLIPLoader", timeout=10).json()
            have = set(info["CLIPLoader"]["input"]["required"]["clip_name"][0])
            return [k for k, m in self.PE_MODELS.items() if self.file(m["slot"]) in have and self.has_node(m["node"])]
        except Exception:
            return list(self.PE_MODELS)

    def build_edit_graph(self, image_name, prompt, seed, steps=None, extra_refs=(), mask_name=None, rgba=False):
        """与官方模板 image_qwen_image_2_1_image_edit 相同（去掉了可选的提示词增强分支）。
        2.1 本来就不用 CFG：cfg=1，每步只跑一次。输出尺寸 = 参考图尺寸（resolution=0）。
        mask_name（白 = 重绘）：原图编码成潜空间 + SetLatentNoiseMask，只重绘遮罩里，其余保留原图（首帧 / 尾帧补空的地方）。"""
        g = {}

        def node(nid, cls, **inputs):
            g[str(nid)] = {"class_type": cls, "inputs": inputs}
            return str(nid)

        Q = self.QWEN
        steps = steps or Q["steps"]
        model = node(2, "QwenImage21Cache", model=[node(1, "UNETLoader", unet_name=Q["unet"], weight_dtype="default"), 0],
                     device=self.QWEN_KV_CACHE, dtype="default")
        clip = node(3, "CLIPLoader", clip_name=Q["clip"], type="qwen_image", device="default")
        vae = node(4, "VAELoader", vae_name=Q["vae"])
        enc = dict(clip=[clip, 0], vae=[vae, 0], prompt=prompt, negative_prompt="", resolution=0,
                   **{"images.image_1": [node(5, "LoadImage", image=image_name), 0]})
        if rgba:                                         # 透明通道：LoadImage 的 MASK = 1 - 透明度，JoinImageWithAlpha 拼回 RGBA
            enc["images.image_1"] = [node(30, "JoinImageWithAlpha", image=["5", 0], alpha=["5", 1]), 0]
        for k, name in enumerate(extra_refs, start=2):
            enc[f"images.image_{k}"] = [node(10 + k, "LoadImage", image=name), 0]
        te = node(6, "TextEncodeQwenImage21", **enc)
        latent = [te, 2]
        if mask_name:
            mk = node(21, "ImageToMask", image=[node(20, "LoadImage", image=mask_name), 0], channel="red")
            latent = [node(23, "SetLatentNoiseMask", samples=[node(22, "VAEEncode", pixels=["5", 0], vae=[vae, 0]), 0],
                           mask=[mk, 0]), 0]
        ks = node(7, "KSampler", model=[model, 0], positive=[te, 0], negative=[te, 1], latent_image=latent,
                  seed=seed, steps=steps, cfg=1.0, sampler_name="euler", scheduler="simple", denoise=1.0)
        node(9, "SaveImage", images=[node(8, "VAEDecode", samples=[ks, 0], vae=[vae, 0]), 0],
             filename_prefix="videops/qwen_edit")
        return g, "9"

    def build_h3img_graph(self, image_names, prompt, seed, width, height, steps=None):
        """MiniMax-H3-Image 改图，和官方模板 image_minimax_h3_image_edit 一样（PE 那一支单独在 rewrite_prompt 里跑）：
        图 1 = 要改的，其余 = 参考（提示词里 <Picture N>）；接了 VAE，参考图也当潜空间锚点；cfg 1、负向 = 零条件、euler / simple。
        画布 = width × height（32 的倍数，调用方按第 1 张图算好）。"""
        m = self.QWEN_MODELS["h3img"]
        g = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": self.file("h3img.unet"), "weight_dtype": "default"}},
             "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": self.file("h3img.clip"), "type": "minimax", "device": "default"}},
             "3": {"class_type": "VAELoader", "inputs": {"vae_name": self.file("h3img.vae")}}}
        cond = dict(clip=["2", 0], vae=["3", 0], prompt=prompt, width=width, height=height)
        for k, name in enumerate(image_names, start=1):
            g[f"1{k:02d}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
            cond[f"images.image_{k}"] = [f"1{k:02d}", 0]
        g["4"] = {"class_type": "MiniMaxH3ImageConditioning", "inputs": cond}
        g["5"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}}
        g["6"] = {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["4", 1],
                                                        "seed": seed, "steps": steps or m["steps"], "cfg": 1.0, "sampler_name": "euler",
                                                        "scheduler": "simple", "denoise": 1.0}}
        g["7"] = {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}}
        g["9"] = {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": "videops/h3img_edit"}}
        return g, "9"

    def h3img_canvas(self, w, h):
        """H3 Image 的画布：第 1 张图等比放到约 4 MP，每边 32 的倍数（官方模板的做法：低分辨率效果差）。"""
        s = (self.H3IMG_PIXELS / (w * h)) ** 0.5
        return max(32, round(w * s / 32) * 32), max(32, round(h * s / 32) * 32)

    def _edit_h3img(self, image_path, prompt, work, seed, log, out_name, refs, mask):
        """MiniMax-H3-Image 改图：第 1 张放大 / 缩小到约 4 MP（32 的倍数）当画布，参考图原样（节点自己缩），出来缩回原图大小。"""
        work = Path(work)
        if not self.has_node("MiniMaxH3ImageConditioning"):
            raise RuntimeError("ComfyUI 上没有 MiniMaxH3ImageConditioning 节点：MiniMax-H3-Image 要 ComfyUI 0.45.0 以上，升级后重启 ComfyUI")
        miss = self.model_missing("h3img")
        if miss:                                          # 先查文件：ComfyUI 拒收的报错只说「不在列表里」，看不出该下什么
            raise RuntimeError("ComfyUI 上缺 MiniMax H3 Image 要用的文件：" + "、".join(miss)
                               + "。下载后放进 ComfyUI/models 对应的文件夹（VAE 放 vae/，文本编码器放 text_encoders/），重启 ComfyUI；或在图片生成的「模型」里换回 Qwen")
        im = Image.open(image_path).convert("RGB")
        w, h = im.size
        W, H = self.h3img_canvas(w, h)
        im.resize((W, H), Image.LANCZOS).save(work / "h3img_in.png")
        if mask is not None:
            log("  MiniMax H3 Image 没有遮罩输入：整张交给模型，出来再按遮罩贴回")
        refs = list(refs)
        if len(refs) > self.H3IMG_MAX_PICS - 1:
            log(f"  注意：MiniMax H3 Image 最多收 {self.H3IMG_MAX_PICS} 张图，后面 {len(refs) - self.H3IMG_MAX_PICS + 1} 张参考图没发")
            refs = refs[: self.H3IMG_MAX_PICS - 1]
        names = [self._upload(work / "h3img_in.png")] + [self._upload(Path(r)) for r in refs]
        graph, out = self.build_h3img_graph(names, prompt, seed, W, H)
        (work / "qwen_graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"  MiniMax H3 Image 改图（画布 {W}x{H}，{len(refs)} 张参考图）…")
        p = self._download(self._run(graph, log), out, work)[0]
        Image.open(p).convert("RGB").resize((w, h), Image.LANCZOS).save(work / out_name)
        p.unlink(missing_ok=True)
        return work / out_name



    # Qwen 2.1 的 KV 缓存（参考图那段注意力结果跨步复用）。auto 时同一个 ComfyUI 进程里连续第二次改图会让 ComfyUI
    # 整个崩溃（2026-10-19 实测两次，崩在 qwen_image21 attn，4090 开着 --use-sage-attention）。off = 每步重算，最稳。
    QWEN_KV_CACHE = "off"
    REF_MAX_PIXELS = 1.0e6              # 参考图（第 2 张起）只是参考：缩到约 100 万像素，省显存和计算

    @property
    def edit_label(self):
        """候选信息里记的改图模型名。"""
        return f"工作流 · {self.image_workflow}" if self.image_workflow else self.qwen_model

    image_workflow = None               # 用户自己的改图工作流（workflows/image/<名字>.json）；None = 内置的 Qwen Image 2.1

    def _edit_workflow(self, image_path, prompt, work, seed, log, out_name, refs, mask):
        """用用户的工作流改图（workflows.py）：图原样上传（不补边、参考图不缩），出来和第 1 张图大小不同就缩回去。
        工作流没有遮罩节点时 mask 不发（调用方按遮罩贴回）；有遮罩节点而这次没遮罩 = 全白（整张重画）。"""
        import workflows as WF
        work = Path(work)
        wf = WF.load(self.image_workflow)
        if not self.has_node(WF.IMG):
            raise RuntimeError("ComfyUI 上没有 Reframe 输入 / 输出节点：把仓库里的 comfyui/ComfyUI-Reframe 整个文件夹拷到 "
                               "ComfyUI 的 custom_nodes 下（覆盖旧的），重启 ComfyUI")
        need = WF.needs(wf)
        if not need["seed"]:
            log("  注意：工作流里没有 Reframe Number Input（seed），一次出的几张候选会一模一样")
        w, h = Image.open(image_path).size
        imgs = [Path(image_path)] + [Path(r) for r in refs]
        if len(imgs) > need["images"]:
            log(f"  注意：工作流只收 {need['images']} 张图，后面 {len(imgs) - need['images']} 张参考图没发")
        mask_name = None
        if need["mask"]:
            if mask is None:
                mask = work / "wf_mask_full.png"
                Image.new("L", (w, h), 255).save(mask)
            mask_name = self._upload(Path(mask))
        graph, out = WF.fill(wf, [self._upload(p) for p in imgs[:need["images"]]], mask_name, prompt, seed, (w, h))
        (work / "qwen_graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"  工作流「{self.image_workflow}」改图…")
        p = self._download(self._run(graph, log), out, work)[0]
        q = Image.open(p).convert("RGB")
        if q.size != (w, h):
            log(f"  注意：工作流输出 {q.size[0]}x{q.size[1]}，缩回原图的 {w}x{h}")
            q = q.resize((w, h), Image.LANCZOS)
        q.save(work / out_name)
        p.unlink(missing_ok=True)
        return work / out_name

    def edit_image(self, image_path, prompt, work, seed=42, log=print, out_name="qwen_edit.png", refs=(), mask=None,
                   alpha=False, pad=True, ref_max=None):
        """refs：额外的参考图（第 2、3… 张，提示词里用 Picture 2 … 指代）。输出尺寸 = 第 1 张图。
        mask：遮罩图（白 = 重绘，和第 1 张图一样大）：只重绘遮罩里，其余保留原图。
        alpha=True：第 1 张图带透明通道（空的地方透明），按 RGBA 交给 Qwen（LoadImage 的透明度经 JoinImageWithAlpha 拼回）。
        Qwen 会把边长取整到 32 的倍数（1038 → 1024）：先镜像补边到 32 的倍数，出来再裁回，不拉伸画面。
        pad=False：不补边，第 1 张图原样上传（首帧 / 尾帧照测试做法，输出由调用方缩回原尺寸）。
        ref_max：参考图缩到多少像素以内（None = REF_MAX_PIXELS；0 = 不缩，原尺寸上传）。"""
        if self.image_workflow:
            return self._edit_workflow(image_path, prompt, work, seed, log, out_name, refs, mask)
        if self.QWEN_MODELS[self.qwen_model].get("family") == "h3":
            return self._edit_h3img(image_path, prompt, work, seed, log, out_name, refs, mask)
        work = Path(work)
        src = np.array(Image.open(image_path).convert("RGBA" if alpha else "RGB"))
        h, w = src.shape[:2]
        ph, pw = (-h % 32, -w % 32) if pad else (0, 0)
        if ph or pw:
            Image.fromarray(cv2.copyMakeBorder(src, 0, ph, 0, pw, cv2.BORDER_REFLECT)).save(work / "qwen_in_padded.png")
            image_path = work / "qwen_in_padded.png"
        if mask is not None:                             # 遮罩跟着补边（补出来的边不重绘）
            mk = np.array(Image.open(mask).convert("L"))
            Image.fromarray(cv2.copyMakeBorder(mk, 0, ph, 0, pw, cv2.BORDER_CONSTANT, value=0)).save(work / "qwen_mask_padded.png")
            mask = work / "qwen_mask_padded.png"
        small = []
        for k, r in enumerate(refs):
            im = Image.open(r).convert("RGB")
            cap = self.REF_MAX_PIXELS if ref_max is None else ref_max
            s = min(1.0, (cap / (im.width * im.height)) ** 0.5) if cap else 1.0
            if s < 1:
                im = im.resize((max(32, round(im.width * s / 32) * 32), max(32, round(im.height * s / 32) * 32)), Image.LANCZOS)
                f = work / f"qwen_ref{k + 2}_small.png"
                im.save(f)
                r = f
            small.append(r)
        graph, out = self.build_edit_graph(self._upload(Path(image_path)), prompt, seed,
                                           extra_refs=[self._upload(Path(r)) for r in small],
                                           mask_name=self._upload(Path(mask)) if mask is not None else None, rgba=alpha)
        (work / "qwen_graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
        p = self._download(self._run(graph, log), out, work)[0]
        q = np.array(Image.open(p).convert("RGB"))
        if not pad:                                      # 不补边：原样交回，调用方缩回原尺寸
            Image.fromarray(q).save(work / out_name)
            p.unlink(missing_ok=True)
            return work / out_name
        if q.shape[:2] != (h + ph, w + pw):
            log(f"  注意：Qwen 输出 {q.shape[1]}x{q.shape[0]}，和补边后的 {w + pw}x{h + ph} 不同，按比例缩放回去")
            q = cv2.resize(q, (w + pw, h + ph), interpolation=cv2.INTER_CUBIC)
        Image.fromarray(q[:h, :w]).save(work / out_name)
        p.unlink(missing_ok=True)
        return work / out_name

    # 看图写描述可选的模型（视频生成「空区分析」下拉；其余看图照旧用默认的 Qwen3-VL 8B）。
    # node = 跑它要的节点：TextGenerateV2 的图片输入是 images.image1，TextGenerate 是 image。
    VL_MODELS = {
        "qwen3vl8b": dict(label="Qwen3-VL 8B", clip="qwen3vl_8b_int8_convrot.safetensors", slot="qwen.clip", type="qwen_image", node="TextGenerate"),
        "pe_qwen": dict(label="Qwen3.5 9B（Qwen 2.1 PE）", clip="qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors",
                        slot="pe.qwen", type="qwen_image", node="TextGenerate"),
        "pe_h3": dict(label="Qwen3.5 9B（H3 Image PE）", clip="qwen3.5_9b_w4a8_gptq.safetensors", slot="pe.h3", type="minimax", node="TextGenerateV2"),
    }

    def vl_available(self):
        """ComfyUI 上能跑的看图模型（文件在 CLIPLoader 列表里，节点也有）。"""
        try:
            info = self.http.get(f"{self.url}/object_info/CLIPLoader", timeout=10).json()
            have = set(info["CLIPLoader"]["input"]["required"]["clip_name"][0])
            return [k for k, m in self.VL_MODELS.items() if self.file(m["slot"]) in have and self.has_node(m["node"])]
        except Exception:
            return list(self.VL_MODELS)

    def describe(self, image_path, question, log=print, max_length=200, model=None):
        """看图回答（ComfyUI 原生 TextGenerate / TextGenerateV2，不采样）。model = VL_MODELS 的键，None = Qwen3-VL 8B。"""
        m = self.VL_MODELS[model or "qwen3vl8b"]
        question = no_emoji(question)
        gen = {"clip": ["1", 0], "max_length": max_length, "prompt": question, "sampling_mode": "off"}
        if m["node"] == "TextGenerateV2":
            gen["images.image1"] = ["2", 0]
        else:
            gen["image"] = ["2", 0]
        g = {
            "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": self.file(m["slot"]), "type": m["type"], "device": "default"}},
            "2": {"class_type": "LoadImage", "inputs": {"image": self._upload(Path(image_path))}},
            "3": {"class_type": m["node"], "inputs": gen},
            "4": {"class_type": "PreviewAny", "inputs": {"source": ["3", 0]}},
        }
        res = self._run(g, log)
        txt = re.sub(r"(?s)<think>.*?</think>", "", (res.get("4", {}).get("text") or [""])[0])
        return " ".join(txt.replace("*", "").split())

    PE_MODEL = "qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors"   # Qwen Image 2.1 官方的编辑提示词改写模型
    PE_SYSTEM = Path(__file__).parent / "prompts" / "qwen_pe_edit_system.txt"   # 系统提示：照官方编辑工作流（用户给的分享工作流）

    def rewrite_prompt(self, images, instruction, log=print, seed=0):
        """官方 PE 模型看图，把一句短指令改写成一段详细的编辑指令（每块空的地方补什么、谁保持不动），交给 Qwen Image 2.1。
        采样参数照官方工作流（thinking 开、temperature 1、top_k 20、top_p 0.95）。images 第 1 张是要改的图，其余是参考（<image2>…）。
        2026-11-02 测 17 张首帧：比固定的基础提示稳（多画人、平涂墙、扩边接缝都少了）。
        pe_model = "h3" 时改用 MiniMax-H3-Image 的 PE（_rewrite_h3img）。"""
        if self.pe_model == "h3":
            return self._rewrite_h3img(images, instruction, log, seed)
        g = {"1": {"class_type": "CLIPLoader", "inputs": {"clip_name": self.file("pe.qwen"), "type": "qwen_image", "device": "default"}},
             "9": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": self.PE_SYSTEM.read_text(encoding="utf-8")}}}
        for k, f in enumerate(images):
            g[f"2{k}"] = {"class_type": "LoadImage", "inputs": {"image": self._upload(Path(f))}}
        img = ["20", 0]
        if len(images) > 1:
            g["30"] = {"class_type": "BatchImagesNode", "inputs": {f"images.image{k}": [f"2{k}", 0] for k in range(len(images))}}
            img = ["30", 0]
        g["3"] = {"class_type": "TextGenerate", "inputs": {
            "clip": ["1", 0], "image": img, "prompt": instruction, "max_length": 4096, "system_prompt": ["9", 0],
            "sampling_mode": "on", "sampling_mode.temperature": 1.0, "sampling_mode.top_k": 20, "sampling_mode.top_p": 0.95,
            "sampling_mode.min_p": 0.0, "sampling_mode.repetition_penalty": 1.0, "sampling_mode.seed": seed,
            "sampling_mode.presence_penalty": 0.0, "thinking": True, "use_default_template": True, "mtp": "auto"}}
        g["4"] = {"class_type": "PreviewAny", "inputs": {"source": ["3", 0]}}
        txt = (self._run(g, log).get("4", {}).get("text") or [""])[0]
        txt = " ".join(re.sub(r"(?s)<think>.*?</think>", "", txt).split())
        m = re.search(r'"rewritten_prompt"\s*:\s*"((?:[^"\\]|\\.)*)"', txt)   # 官方 PE 有时按系统提示的格式包一层 JSON
        return json.loads(f'"{m.group(1)}"') if m else txt

    H3PE_SYSTEM = Path(__file__).parent / "prompts" / "h3img_pe_system.txt"   # ComfyUI 官方模板 image_minimax_h3_image_edit 里的（MIT）
    # 任务提示词是 MiniMax 的（MiniMax-H3-Image Community License），不放进仓库：第一次用时从官方 PE 仓库下载、按官方 manifest 的
    # sha256 核对，存在 app/prompts/third_party/（.gitignore）。
    H3PE_TASK = Path(__file__).parent / "prompts" / "third_party" / "h3img_pe_edit_task_prompt.txt"
    H3PE_TASK_URL = "https://huggingface.co/MiniMaxAI/MiniMax-H3-Image-PE/resolve/main/prompt/edit/task_prompt.txt"
    H3PE_TASK_SHA256 = "c5695dbfc07a9e65555cef67d92467c613371cc2039b96f057022246a30a57b2"

    def h3pe_task(self, log=print):
        """H3 Image PE 的任务提示词（见上）。下载失败 / 校验不对就报错，不拿别的凑。"""
        import hashlib
        f = self.H3PE_TASK
        if not f.exists():
            log("  第一次用 H3 Image PE：从 MiniMax 官方 PE 仓库下载任务提示词…")
            try:
                r = requests.get(self.H3PE_TASK_URL, timeout=60)           # 外网：照系统代理走
                r.raise_for_status()
            except Exception as e:
                raise RuntimeError(f"下载 H3 Image PE 提示词失败（{e}）：可以手动下载 {self.H3PE_TASK_URL} 放到 {f}")
            if hashlib.sha256(r.content).hexdigest() != self.H3PE_TASK_SHA256:
                raise RuntimeError(f"下载的 H3 Image PE 提示词和官方校验值对不上（官方可能更新了）：手动下载 {self.H3PE_TASK_URL} 放到 {f}")
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(r.content)
        return f.read_text(encoding="utf-8")

    def _rewrite_h3img(self, images, instruction, log=print, seed=0):
        """MiniMax-H3-Image 的 PE（Qwen3.5 9B，TextGenerateV2）：看图，把短指令写成目标图的完整描述（JSON 的 expanded_caption）。
        图在 PE 里叫 <Picture N>：Qwen PE 写法的 <imageN> 换成 <Picture N>。画幅比例固定成第 1 张图的（软件里不改画布）。"""
        if not self.has_node("TextGenerateV2"):
            raise RuntimeError("ComfyUI 上没有 TextGenerateV2 节点：H3 Image PE 要 ComfyUI 0.45.0 以上，升级后重启 ComfyUI")
        instruction = re.sub(r"<image(\d+)>", r"<Picture \1>", instruction)
        w, h = Image.open(images[0]).size
        gcd = math.gcd(w, h)
        user = f"User Editing Instruction: {json.dumps(instruction, ensure_ascii=False)}\nUser Aspect Ratio Selection: {w // gcd}:{h // gcd}"
        task = f"{self.h3pe_task(log).rstrip()}\nUser input: {user}\noutput_text:"     # 和 ComfyUI 官方模板一样接在末尾
        g = {"1": {"class_type": "CLIPLoader", "inputs": {"clip_name": self.file("pe.h3"), "type": "minimax", "device": "default"}},
             "9": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": self.H3PE_SYSTEM.read_text(encoding="utf-8")}}}
        gen = {"clip": ["1", 0], "prompt": task, "max_length": 8196, "system_prompt": ["9", 0],
               "sampling_mode": "on", "sampling_mode.temperature": 1.0, "sampling_mode.top_k": 20, "sampling_mode.top_p": 0.95,
               "sampling_mode.min_p": 0.0, "sampling_mode.repetition_penalty": 1.0, "sampling_mode.presence_penalty": 0.0,
               "sampling_mode.seed": seed, "thinking": True, "use_default_template": True, "mtp": "auto"}
        for k, f in enumerate(images, start=1):
            g[f"2{k:02d}"] = {"class_type": "LoadImage", "inputs": {"image": self._upload(Path(f))}}
            gen[f"images.image{k}"] = [f"2{k:02d}", 0]
        g["3"] = {"class_type": "TextGenerateV2", "inputs": gen}
        g["4"] = {"class_type": "PreviewAny", "inputs": {"source": ["3", 0]}}
        log("  H3 Image PE 改写提示词…")
        return h3img_caption((self._run(g, log).get("4", {}).get("text") or [""])[0])

    def inpaint_levels(self, p, work, meta, levels, log=print, ref_videos=()):
        """H3 重绘。levels = [(像素遮罩 (n,h,w) bool, 值 0~255), …]，合成时只有一项：重绘区 = 255。
        工作流里 ThresholdMask 0.5 会二值化，所以中间值没有意义。p.mask 必须是所有区域的并集。"""
        info, mpath, counts = self.write_inpaint_inputs(p, work, meta, levels, log)
        F, n = info["F"], info["n"]
        part = getattr(p, "partial", None)
        if part is not None:
            self._need_partial()
        if len(p.refs) > 9:
            log(f"  注意：H3 最多收 9 张参考图，后面 {len(p.refs) - 9} 张没发（提示词里的对应 Picture 编号会落空）")
        for k, frame in getattr(p, "pins", []):
            if k >= 9:
                log(f"  注意：钉在第 {frame} 帧的关键帧是第 {k + 1} 张参考图，超过 9 张没发，这次没钉上")
        refs = [self._upload(Path(r)) for r in p.refs[:9]]
        vids = [self._upload(Path(v)) for v in list(ref_videos)[:3]]
        graph, out_node = self.build_h3_graph(self._upload(work / "h3_input.mp4"), self._upload(mpath), p.prompt,
                                              F, info["fps"], p.seed, refs, ref_video_names=vids,
                                              partial_name=self._upload(info["partial"]) if part is not None else None,
                                              partial_strength=part[1] if part is not None else 0.0, pins=getattr(p, "pins", ()))
        (work / "comfy_graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
        self.write_inputs_used(work, graph, p, [f"重绘区像素数 {counts}",
                                                f"原片 {n} 帧 → 补到 {F} 帧，{info['fps']} fps"]
                               + ([f"主体重绘强度 {part[1]:.2f}：本体（保护蒙版除外）部分重绘（h3_partial_mask.mov）"] if part is not None else []))
        first = self._download(self._run(graph, log), out_node, work)[0]
        if getattr(p, "pass2", None):
            return self.second_pass(p, work, meta, levels, first, log, ref_videos)
        return first

    def _need_partial(self):
        if not self.node_has_input(self.H3_MASK_NODE, "partial_mask"):
            raise RuntimeError("「主体重绘强度」要新版节点包：把本仓库 comfyui/ComfyUI-Reframe 整个文件夹覆盖拷到 ComfyUI 的 "
                               "custom_nodes 下，重启 ComfyUI（或把主体重绘强度调回 0）")

    def second_pass(self, p, work, meta, levels, first, log=print, ref_videos=()):
        """二次采样（2026-11-18 用户定，默认关）：一采的结果放大到二采分辨率当底，在更高分辨率上再跑一遍 H3，只在重绘区加噪，
        denoise = p.pass2["denoise"]。保留区（本体等，像素 mask = 0）用这次合成输入（粗剪）的高清像素，比一采放大清楚。
        提示词、参考图、<Video 1>、LoRA、seed、步数都和一采一样。二采分辨率 = p.pass2["preset"]（分辨率预设，按画幅比例放进去）。
        二采画布不比一采大时跳过，直接用一采。"""
        cfg = p.pass2
        W1, H1 = h3_canvas(meta["w"], meta["h"])
        W2, H2 = preset_canvas(meta["w"], meta["h"], str(cfg.get("preset") or "1080"))
        d = float(cfg.get("denoise", 0.5))
        if W2 * H2 <= W1 * H1:
            log(f"  二次采样：二采画布 {W2}x{H2} 不比一采 {W1}x{H1} 大，跳过，直接用一采")
            return first
        check_cancel()
        work2 = Path(work) / "二采pass2"
        work2.mkdir(exist_ok=True)
        frames, fps = read_video(p.input_video)
        g1, _ = read_video(first)
        n, F = len(frames), h3_frames(len(frames))
        pad = lambda a: np.concatenate([a, np.repeat(a[-1:], F - len(a), 0)]) if len(a) < F else a[:F]
        base = pad(np.stack([cv2.resize(f, (W2, H2), interpolation=cv2.INTER_AREA) for f in frames]))
        up = pad(np.stack([cv2.resize(f, (W2, H2), interpolation=cv2.INTER_LANCZOS4) for f in g1]))
        m = np.zeros((n, H2, W2), bool)
        for lv, _ in levels:                              # 一采的重绘区（各强度的并集），放大到二采画布
            m |= np.stack([cv2.resize(x.astype(np.uint8), (W2, H2), interpolation=cv2.INTER_NEAREST) > 0 for x in lv])
        m = pad(m)
        part = getattr(p, "partial", None)
        pm = None
        if part is not None:                              # 主体部分重绘过：二采也用一采结果当底、同样部分重绘，不然又变回原片的光
            pm = pad(np.stack([cv2.resize(x.astype(np.uint8), (W2, H2), interpolation=cv2.INTER_NEAREST) > 0 for x in part[0]])) & ~m
            base[pm] = up[pm]
        base[m] = up[m]                                   # 重绘区用一采结果当底，保留区用高清原像素
        write_video(work2 / "h3_input.mp4", base, fps, crf=10)
        if getattr(p, "audio", None) is not None:
            add_audio(work2 / "h3_input.mp4", p.audio, fps)
        mpath = write_pixel_mask(work2 / "h3_pixel_mask.mov", m.astype(np.uint8) * 255, fps)
        ppath = write_pixel_mask(work2 / "h3_partial_mask.mov", pm.astype(np.uint8) * 255, fps) if pm is not None else None
        log(f"  二次采样：{W1}x{H1} → {W2}x{H2}，{F} 帧，重绘区 {m.mean() * 100:.0f}% 用一采结果当底、只在这里加噪，denoise {d}")
        refs = [self._upload(Path(r)) for r in p.refs[:9]]
        vids = [self._upload(Path(v)) for v in list(ref_videos)[:3]]
        graph, out_node = self.build_h3_graph(self._upload(work2 / "h3_input.mp4"), self._upload(mpath), p.prompt,
                                              F, fps, p.seed, refs, ref_video_names=vids, denoise=d,
                                              partial_name=self._upload(ppath) if ppath else None,
                                              partial_strength=part[1] if ppath else 0.0, pins=getattr(p, "pins", ()))
        (work2 / "comfy_graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
        self.write_inputs_used(work2, graph, p, [f"二次采样：一采 {W1}x{H1} → 二采 {W2}x{H2}，denoise {d}",
                                                 f"二采的底：重绘区 = 一采结果放大（{first.name}），其余 = 合成输入的高清像素"])
        return self._download(self._run(graph, log), out_node, work2)[0]


    def run_video_workflow(self, name, wf, p, work, size, mask, guide, prompts, log=print):
        """用户自己的视频工作流（workflows/video/<name>.json，workflows.fill_video）。粗剪缩到软件算好的画布
        （h3_canvas：跟「输出分辨率」设置走），帧数 = 原片帧数（模型要 4k+1 之类由工作流自己补）；mask 同大小、png 编码。
        只上传工作流用得到的东西。返回下载下来的成片路径。"""
        import workflows as WF
        work = Path(work)
        if not self.has_node(WF.VID):
            raise RuntimeError("ComfyUI 上没有 Reframe Video Input 节点：把仓库里的 comfyui/ComfyUI-Reframe 整个文件夹拷到 "
                               "ComfyUI 的 custom_nodes 下（覆盖旧的），重启 ComfyUI")
        need = WF.needs_video(wf)
        if len(p.refs) < need["images"]:                # 先查再上传：报错时 _run 的清理不会跑，传上去的粗剪会留在 ComfyUI 上
            raise RuntimeError(f"这个工作流要 {need['images']} 张参考图，这次只勾了 {len(p.refs)} 张")
        frames, fps = read_video(p.input_video)
        n = len(frames)
        W, H = h3_canvas(*size)
        write_video(work / "wf_rough_cut.mp4", np.stack([cv2.resize(f, (W, H), interpolation=cv2.INTER_AREA) for f in frames]),
                    fps, crf=10)
        if getattr(p, "audio", None) is not None:
            add_audio(work / "wf_rough_cut.mp4", p.audio, fps)
        videos = {"rough_cut": self._upload(work / "wf_rough_cut.mp4")}
        if "guide" in need["videos"]:
            videos["guide"] = self._upload(Path(guide))
        mask_name = None
        if need["mask"]:
            m = np.stack([cv2.resize(x.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST) for x in mask]) * 255
            mask_name = self._upload(write_pixel_mask(work / "wf_mask.mov", m.astype(np.uint8), fps))
        refs = list(p.refs)
        if len(refs) > need["images"]:
            log(f"  注意：工作流只收 {need['images']} 张参考图，后面 {len(refs) - need['images']} 张没发")
        if not need["seed"]:
            log("  注意：工作流里没有 Reframe Number Input（seed），面板上的 Seed 不起作用")
        imgs = [self._upload(Path(r)) for r in refs[:need["images"]]]
        graph, out = WF.fill_video(wf, videos, mask_name, imgs, prompts, dict(seed=p.seed, width=W, height=H, frames=n), fps)
        (work / "comfy_graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
        lines = [f"工作流：{name}（workflows/video/{name}.json）", f"粗剪 rough_cut：{W}x{H}，{n} 帧，{fps:g} fps（wf_rough_cut.mp4）"]
        if "guide" in need["videos"]:
            lines.append(f"guide（<Video 1>）：{guide}")
        if need["mask"]:
            lines.append("重绘 mask：wf_mask.mov（白 = 重绘）")
        lines += [f"Picture {k}：{lab}（{f}）" for k, (f, lab) in enumerate(zip(refs[:need["images"]], p.ref_labels), start=1)]
        for t in need["texts"]:
            lines += ["", f"【提示词 {t}】", prompts.get(t, "")]
        (work / "inputused.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        log(f"  工作流「{name}」：{W}x{H}，{n} 帧，参考图 {min(len(refs), need['images'])} 张")
        return self._download(self._run(graph, log), out, work)[0]

    def write_inpaint_inputs(self, p, work, meta, levels, log=print):
        """写 H3 直接用的两份输入：h3_input.mp4（画布大小、17k+5 帧）和 h3_pixel_mask.mov（png 编码、无损）。
        生成（inpaint_levels）和「导出」共用。返回 (info, 遮罩路径, 各强度像素数)。"""
        info = self.prepare_inpaint(p, work, meta)
        F, W, H, n = info["F"], info["W"], info["H"], info["n"]
        rs = lambda m: np.stack([cv2.resize(x.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST) > 0 for x in m])
        pad = lambda m: np.concatenate([m, np.repeat(m[-1:], F - n, 0)]) if F > n else m
        pix = np.zeros((F, H, W), np.uint8)              # 像素级：每个像素的强度。一个潜空间格取格内最大值，由 ComfyUI 里的节点做
        for m, v in levels:
            pm = pad(rs(m))
            np.maximum(pix, pm.astype(np.uint8) * int(v), out=pix)
        counts = {int(v): int((pix == v).sum()) for _, v in levels}
        log(f"  H3 画布 {W}x{H}，{n}→{F} 帧；各强度的像素数 {counts}；像素 mask 交给 ComfyUI 里的 Reframe H3 Inpaint Mask 转潜空间")
        mpath = write_pixel_mask(work / "h3_pixel_mask.mov", pix, info["fps"])
        part = getattr(p, "partial", None)
        if part is not None:                             # 主体重绘强度：本体（保护蒙版除外）部分重绘，同样交给节点按格换算
            pm = pad(rs(part[0])) & (pix == 0)
            info["partial"] = write_pixel_mask(work / "h3_partial_mask.mov", pm.astype(np.uint8) * 255, info["fps"])
            log(f"  主体重绘强度 {part[1]:.2f}：部分重绘的本体像素 {int(pm.sum())}（h3_partial_mask.mov）")
        return info, mpath, counts

    # ---------- H3 局部重绘 ----------
    def prepare_inpaint(self, p, work, meta):
        """把输入整理成 H3 直接能用的格式：画布尺寸、17k+5 帧、潜空间遮罩。"""
        frames, fps = read_video(p.input_video)
        n = len(frames)
        W, H = h3_canvas(meta["w"], meta["h"])
        F = h3_frames(n)
        vid = np.stack([cv2.resize(f, (W, H), interpolation=cv2.INTER_AREA) for f in frames])
        msk = np.stack([cv2.resize(m.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST) > 0 for m in p.mask])
        if F > n:                                       # 补帧：重复最后一帧，遮罩也沿用最后一帧
            vid = np.concatenate([vid, np.repeat(vid[-1:], F - n, 0)])   # （标成“保留”会把结尾的重绘拖住）
            msk = np.concatenate([msk, np.repeat(msk[-1:], F - n, 0)])
        write_video(work / "h3_input.mp4", vid, fps, crf=10)
        if getattr(p, "audio", None) is not None:       # NKDAVLatent：原片音频一起编码（audio_mode=keep，音频不重绘）
            add_audio(work / "h3_input.mp4", p.audio, fps)
        write_pixel_mask(work / "h3_pixel_mask.mov", msk.astype(np.uint8) * 255, fps)   # 像素 mask；潜空间由 ComfyUI 里的 ReframeH3InpaintMask 换算
        return dict(W=W, H=H, F=F, n=n, fps=fps, pixels=int(msk.sum()))


    H3_STEPS = 20        # H3 采样步数（「视频生成」面板可改，存 app_settings.json 的 h3_steps；加速 LoRA 用少步数）

    H3_MASK_NODE = "ReframeH3InpaintMask"   # 节点包 ComfyUI-Reframe（本仓库 comfyui/ComfyUI-Reframe，V3 写法，无第三方依赖）

    def build_h3_graph(self, video_name, mask_name, prompt, F, fps, seed, ref_names=(), ref_video_names=(), steps=None, check_nodes=True,
                       denoise=1.0, partial_name=None, partial_strength=0.0, pins=()):
        """合成用的 H3 工作流：像素 mask → ReframeH3InpaintMask（按 H3 的 32px 格 / 时间组换算潜空间 mask，
        直接带在 latent 上）→ 采样 → 解码。画布 = h3_input.mp4 整幅。音频那一半取 ReferenceToVideo 的空音频。
        LoRA / 主模型照「视频生成」栏的设置（_apply_loras / self.unet）。
        pins = [(参考图下标, 帧号)]：关键帧钉帧，那张参考图（节点 30 + 下标）再用 MiniMaxH3AddGuide 钉在这一帧（节点 80 起，串在 BasicGuider 前）。
        2026-11-08 起改用 ReframeH3InpaintMask：47bd/028 同输入同 seed 成片逐像素一样。"""
        g = {}

        def node(nid, cls, **inputs):
            g[str(nid)] = {"class_type": cls, "inputs": inputs}
            return str(nid)

        unet = self._apply_loras(node, node(1, "UNETLoader", unet_name=self.file("h3.unet"), weight_dtype="default"))
        clip = node(2, "CLIPLoader", clip_name=self.file("h3.clip"), type="minimax", device="default")
        vae = node(3, "VAELoader", vae_name=self.file("h3.vae"))
        avae = node(4, "VAELoader", vae_name=self.file("h3.audio_vae"))
        src = node(6, "GetVideoComponents", video=[node(5, "LoadVideo", file=video_name), 0])
        mvid = node(8, "GetVideoComponents", video=[node(7, "LoadVideo", file=mask_name), 0])
        redraw = node(9, "ImageToMask", image=[mvid, 0], channel="red")
        if check_nodes and not self.has_node(self.H3_MASK_NODE):      # 导出（只写工作流文件）不查 4090
            raise RuntimeError(f"4090 的 ComfyUI 没有 {self.H3_MASK_NODE} 节点：把本仓库 comfyui/ComfyUI-Reframe "
                               "整个文件夹拷到 4090 ComfyUI 的 custom_nodes 下，重启 ComfyUI")
        size = node(41, "GetImageSize", image=[src, 0])
        encoded = node(10, "VAEEncode", pixels=[src, 0], vae=[vae, 0])
        extra = {}
        if partial_name and partial_strength > 0:      # 主体重绘强度：节点把这些格的 noise mask 设成 strength（H3 按 m·sigma 部分重绘）
            pvid = node(71, "GetVideoComponents", video=[node(70, "LoadVideo", file=partial_name), 0])
            extra = dict(partial_mask=[node(72, "ImageToMask", image=[pvid, 0], channel="red"), 0],
                         partial_strength=round(float(partial_strength), 3))
        masked = node(42, self.H3_MASK_NODE, samples=[encoded, 0], vae=[vae, 0], redraw_mask=[redraw, 0],
                      conflict="redraw", **extra)
        # 2026-11-10 去掉本体 mask（protect_mask）：conflict=redraw 时节点按格只看 redraw（final = r），本体 mask 从来不改结果；
        # 00ca / 592e 试验去掉后成片几乎一样（tests/简化/）。不重绘的地方本来就不在像素 mask 里（本体、保护蒙版都是 0）。
        r2v = dict(clip=[clip, 0], vae=[vae, 0], audio_vae=[avae, 0], prompt=prompt, width=[size, 0], height=[size, 1],
                   length=F, ref_image_size="match")
        for k, name in enumerate(ref_names):
            r2v[f"ref_images.ref_image_{k}"] = [node(30 + k, "LoadImage", image=name), 0]
        for k, name in enumerate(ref_video_names):
            r2v[f"ref_videos.ref_video_{k}"] = [node(60 + k, "GetVideoComponents", video=[node(50 + k, "LoadVideo", file=name), 0]), 0]
        cond = node(12, "MiniMaxH3ReferenceToVideo", **r2v)
        av = node(14, "LTXVConcatAVLatent", video_latent=[masked, 0],
                  audio_latent=[node(13, "LTXVSeparateAVLatent", av_latent=[cond, 1]), 1])
        guided = [cond, 0]
        for j, (k, frame) in enumerate(p for p in pins if 0 <= p[0] < len(ref_names)):   # 关键帧钉帧：keyframe 条件行的时间 = 目标第 frame 帧
            guided = [node(80 + j, "MiniMaxH3AddGuide", positive=guided, vae=[vae, 0], latent=[av, 0],
                           image=[str(30 + k), 0], frame_idx=int(frame)), 0]
        out = node(19, "SamplerCustomAdvanced",
                   noise=[node(15, "RandomNoise", noise_seed=seed), 0],
                   guider=[node(16, "BasicGuider", model=[unet, 0], conditioning=guided), 0],
                   sampler=[node(17, "KSamplerSelect", sampler_name="res_multistep"), 0],
                   sigmas=[node(18, "BasicScheduler", model=[unet, 0], scheduler="simple", steps=steps or self.H3_STEPS,
                                       denoise=1 if denoise >= 1 else float(denoise)), 0],
                   latent_image=[av, 0])
        full = node(21, "VAEDecode", samples=[node(20, "LTXVSeparateAVLatent", av_latent=[out, 0]), 0], vae=[vae, 0])
        node(23, "SaveVideo", video=[node(22, "CreateVideo", images=[full, 0], fps=float(fps), bit_depth="auto",
                                          color_space="sRGB", codec="none"), 0],
             filename_prefix="videops/inpaint", format="auto", codec="auto", **{"format.codec": "auto"})
        return g, "23"

    def detect_image(self, image_path, text, work, threshold=0.4, log=print):
        """单张图片的 SAM 3.1 文本识别：返回每个实例的遮罩（bool 数组列表）。"""
        g = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": self.SAM_CKPT}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["1", 1]}},
            "3": {"class_type": "LoadImage", "inputs": {"image": self._upload(Path(image_path))}},
            "4": {"class_type": "SAM3_Detect", "inputs": {"model": ["1", 0], "image": ["3", 0], "conditioning": ["2", 0],
                                                          "threshold": threshold, "refine_iterations": 2,
                                                          "individual_masks": True}},
            "5": {"class_type": "MaskToImage", "inputs": {"mask": ["4", 0]}},
            "6": {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": "videops/detect"}},
        }
        try:
            res = self._run(g, log)
        except RuntimeError as e:                        # 一个都没识别到时 SaveImage 收到空批次会报错
            if "index 0 is out of bounds" in str(e):
                return []
            raise
        if not res.get("6"):
            return []
        work.mkdir(parents=True, exist_ok=True)
        files = self._download(res, "6", work)
        from PIL import Image
        masks = [np.asarray(Image.open(f).convert("L")) > 127 for f in files]
        for f in files:
            f.unlink()
        return [m for m in masks if m.any()]


