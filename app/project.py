"""项目存储：版本（每次操作生成一个新版本的视频 + 预览帧）、物体（遮罩）。

目录结构
projects/<pid>/project.json
projects/<pid>/versions/<vid>/video.mp4 + proxy/00000.jpg ...
projects/<pid>/masks/<mask_id>.npz      物体遮罩，原分辨率，packbits
projects/<pid>/refs/                    参考图
projects/<pid>/passes/<n>/              每次重绘的输入、遮罩、输出，方便排查
"""
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

import cv2
import numpy as np

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"
MASK_CACHE_MB = 4096               # 遮罩缓存上限（整段原分辨率遮罩一份就是 n×h×w 字节，1080p 150 帧约 300 MB）

CLEAN_OPTS = ("fill_holes", "despeckle", "smooth_t")   # 蒙版清理（图层字段，默认 0）：补小孔 px / 去碎块 % / 时间平滑 ±帧


def clean_opts(o):
    return tuple(float(o.get(k) or 0) for k in CLEAN_OPTS)


def clean_masks(masks, fill_holes=0, despeckle=0, smooth_t=0):
    """SAM 遮罩清理（纯几何，不认物体）：
    fill_holes = 补上面积不超过直径 N px 圆的内部小孔（不碰贴着画面边的空隙）；
    despeckle  = 去掉面积小于这一帧最大那块 N% 的零碎小块；
    smooth_t   = 前后各 N 帧多数票（这一帧 + 前后 2N 帧里超过一半是遮罩才算），去抖动。"""
    out = masks
    if fill_holes or despeckle:
        area = np.pi * (fill_holes / 2) ** 2
        frames = []
        for m in masks:
            if not m.any():
                frames.append(m)
                continue
            m = m.copy()
            if fill_holes:
                n, lab, st, _ = cv2.connectedComponentsWithStats((~m).astype(np.uint8), connectivity=4)
                H, W = m.shape
                for c in range(1, n):
                    x, y, w, h, a = st[c]
                    if a <= area and x > 0 and y > 0 and x + w < W and y + h < H:
                        m[lab == c] = True
            if despeckle:
                n, lab, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
                if n > 2:
                    big = st[1:, cv2.CC_STAT_AREA].max()
                    small = [c for c in range(1, n) if st[c, cv2.CC_STAT_AREA] < big * despeckle / 100]
                    if small:
                        m[np.isin(lab, small)] = False
            frames.append(m)
        out = np.stack(frames)
    k = int(smooth_t)
    if k > 0 and len(out) > 1:
        n = len(out)
        cnt = np.zeros(out.shape[1:], np.uint16)
        res = np.empty_like(out)
        lo, hi = 0, -1                                    # 窗口 [lo, hi] 里的帧已经加进 cnt
        for t in range(n):
            a, b = max(0, t - k), min(n - 1, t + k)
            while hi < b:
                hi += 1
                cnt += out[hi]
            while lo < a:
                cnt -= out[lo]
                lo += 1
            res[t] = cnt * 2 > (b - a + 1)
        out = res
    return out


LAYOUT = 3                         # 新建项目用的文件夹结构（见 Project 的路径方法）

PROXY_W = 960                      # 原来固定的画布预览宽度（proxy/ 文件夹就是这个宽）
WORK_LONG = {"v": 1920}            # 导入视频的工作分辨率：长边超过它就先缩到它（2026-11-12 用户定 1920；0 = 原尺寸）。
                                   # SAM、蒙版、粗剪、首帧都按工作分辨率算；H3 画布短边最多 768，更高的分辨率进不了成片。server 启动时从设置读
PREVIEW = {"w": PROXY_W}           # 设置里选的画布预览宽度（server 启动时从 app_settings.json 读）；为了画布操作流畅，预览是缩小过的
PREVIEW_CHOICES = (960, 1280, 1920)
_proxy_lock = threading.Lock()
PALETTE = ["#ff5a5a", "#4da3ff", "#4cd98a", "#ffc93c", "#c77dff", "#ff8fd1", "#5ce1e6", "#ff9f43"]


# ---------- 视频读写（subprocess 一律读 bytes，按 utf-8 解码，避免中文 Windows 的 GBK 问题）----------

def probe(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height,r_frame_rate",
                          "-of", "json", str(path)], capture_output=True, check=True).stdout
    s = json.loads(out.decode("utf-8"))["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    return s["width"], s["height"], float(num) / float(den)


def read_video(path):
    w, h, fps = probe(path)
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-fps_mode", "passthrough", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout   # 不补帧，和 ComfyUI 读到的一致
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3).copy(), fps


def write_video(path, frames, fps, audio_from=None, crf=12):
    h, w = frames[0].shape[:2]
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
    if audio_from:
        cmd += ["-i", str(audio_from), "-map", "0:v", "-map", "1:a?", "-c:a", "copy"]
    if w % 2 or h % 2:                                   # h264（yuv420p）宽高必须是偶数：奇数的裁掉最后一行 / 列
        cmd += ["-vf", f"crop={w - w % 2}:{h - h % 2}:0:0"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf), str(path)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for f in frames:
            p.stdin.write(np.ascontiguousarray(f).tobytes())
        p.stdin.close()
    except BrokenPipeError:
        pass
    err = p.stderr.read()
    if p.wait() != 0:
        raise RuntimeError(err.decode("utf-8", "replace"))


def write_mask_video(path, masks, fps):
    write_video(path, [np.repeat(m[..., None], 3, 2).astype(np.uint8) * 255 for m in masks], fps, crf=0)


def make_proxy(video, outdir, w=PROXY_W):
    outdir.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(video), "-fps_mode", "passthrough", "-vf", f"scale={w}:-2",
                    "-q:v", "3", str(outdir / "%05d.jpg")], capture_output=True, check=True)
    return len(list(outdir.glob("*.jpg")))


# ---------- 项目 ----------

ACTION_LOG = {"on": True}            # 操作记录开关（设置里改，存 app_settings.json 的 action_log；默认开）
ACTION_LOG_FILE = "操作记录log.jsonl"
ACTION_LOG_MB = 5                     # 超过就把旧的改名（操作记录log_<时间>.jsonl）、另起一个

# 图层属性：tf = 变换（相对物体整体外框中心：平移 x/y 像素、缩放 sx/sy（负数即翻转）、旋转 rot 度）
LAYER_DEFAULTS = dict(tf={"x": 0, "y": 0, "sx": 1, "sy": 1, "rot": 0}, opacity=1.0, visible=True, locked=False, z=None,
                      edge="mix", grow=3)   # 边缘：sam = SAM 硬边；matte = MatAnyone2 柔边；mix = 内部实心 + 外圈柔边
                                            # grow：边缘外扩像素（原画面像素，负数 = 往里收）。这里的 3 只给缺这个字段的旧图层补（不改旧项目）
NEW_LAYER_GROW = 0                          # 新识别的主体 / 部位：主体微调「收缩 / 扩展」默认 0（2026-11-12 用户定）


# 新识别出来的主体的蒙版默认值（2026-11-11 用户定）：轮廓重绘往外 3 px、留空形状矩形 ×3。
# 只在新建图层时写进去（不放 LAYER_DEFAULTS：那个打开旧项目时会补进缺字段的旧图层，会改掉旧项目的分区）；画的蒙版图层建好后改回 0。
SUBJECT_MASK_DEFAULTS = dict(ring_redraw=3, hole_rect=3)


def frame_bboxes(masks):
    """每帧的外框 [x0, y0, x1, y1]，该帧没有物体时为 None。"""
    out = []
    for m in masks:
        ys, xs = np.nonzero(m)
        out.append([int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if len(xs) else None)
    return out


def obj_word(o):
    return (o.get("prompt") or o["name"]).strip()


def expose_original(root, source):
    target = root / "原视频.mp4"
    try:
        target.hardlink_to(source)
    except FileExistsError:
        return
    except OSError:
        if not target.exists():
            shutil.copy2(source, target)


DEDUPE_MIN = 1 << 20               # 小于 1MB 的文件不做硬链接去重（省得碎）

def _md5(path):
    m = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                return m.hexdigest()
            m.update(b)


def write_json_atomic(path, data):
    """先写同目录的临时文件再换名：写到一半断电 / 崩溃也不会留下空的 / 半截的 JSON（project.json、sequence.json、设置）。
    别的线程正在改 data 时 json.dumps 可能报「dict changed size」：重试几次。"""
    path = Path(path)
    for k in range(5):
        try:
            text = json.dumps(data, ensure_ascii=False, indent=1)
            break
        except RuntimeError:
            if k == 4:
                raise
            time.sleep(0.05)
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(text, encoding="utf-8")
    replace_retry(tmp, path, text)


def replace_retry(tmp, path, text=None, tries=5):
    """os.replace。Windows 上目标正被别人开着（别的线程读、杀毒软件、索引）时换名会 PermissionError：稍等重试；
    还不行且给了 text 就退回原地写（和以前一样，不比原来差），没给 text 就删掉临时文件再报错。"""
    for k in range(tries):
        try:
            os.replace(tmp, path)
            return
        except OSError:
            if k < tries - 1:
                time.sleep(0.05 * (k + 1))
    Path(tmp).unlink(missing_ok=True)
    if text is None:
        raise PermissionError(f"写不进 {Path(path).name}：文件被别的程序占用")
    Path(path).write_text(text, encoding="utf-8")


def same_bytes(a, b):
    """两个文件逐字节相同（先比大小，再比 md5）。"""
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except OSError:
        return False
    return _md5(a) == _md5(b)


def _arrays(v, out):
    """缓存值里的 numpy 数组（按 id 去重：裁黑边的条目还引用着底下那份遮罩，不能算两次）。"""
    if isinstance(v, np.ndarray):
        out[id(v)] = v.nbytes
    elif isinstance(v, (tuple, list)):
        for x in v:
            _arrays(x, out)
    elif isinstance(v, dict):
        for x in v.values():
            _arrays(x, out)
    return out


class MaskCache(dict):
    """遮罩缓存：超过 MASK_CACHE_MB 就从最久没用的删起（刚放进去的那条不删）。拖「蒙版扩展」滑杆时每个值一份整段遮罩，
    以前全留着、只增不减（592e 拖 7 下涨到 5.9 GB）。删掉的只是缓存：正在用它的地方（比如生成时的 _Layout）手里的引用照常有效，
    下次要用再从盘上读 / 重新算。请求是多线程的，所以加锁。"""

    def __init__(self, cap_mb=MASK_CACHE_MB):
        super().__init__()
        self.cap = cap_mb * 2**20
        self._lk = threading.RLock()

    def __getitem__(self, k):
        with self._lk:
            v = super().pop(k)               # 读一次 = 挪到最新
            super().__setitem__(k, v)
            return v

    def get(self, k, default=None):
        with self._lk:
            return self[k] if k in self else default

    def nbytes(self):
        with self._lk:
            out = {}
            for v in self.values():
                _arrays(v, out)
            return sum(out.values())

    def __setitem__(self, k, v):
        with self._lk:
            super().pop(k, None)
            super().__setitem__(k, v)
            while len(self) > 1 and self.nbytes() > self.cap:
                super().pop(next(iter(self)))

    def __delitem__(self, k):
        with self._lk:
            super().__delitem__(k)

    def clear(self):
        with self._lk:
            super().clear()


def _rmdir_empty(path: Path):
    """把空目录从里往外删掉（还有文件就都留着）。"""
    if not path.is_dir():
        return
    for f in list(path.iterdir()):
        if f.is_dir():
            _rmdir_empty(f)
    try:
        path.rmdir()
    except OSError:
        pass


def _merge_move(src: Path, dst: Path):
    """把 src 里的东西并进 dst（同名文件已存在就跳过；目录递归并进去，最后删掉空壳）。"""
    if not src.exists() or src == dst:
        return
    dst.mkdir(parents=True, exist_ok=True)
    for f in list(src.iterdir()):
        t = dst / f.name
        if f.is_dir():
            _merge_move(f, t)
            try:
                f.rmdir()
            except OSError:
                pass
        elif not t.exists():
            try:
                f.rename(t)
            except OSError:
                shutil.move(str(f), str(t))


class Project:
    def __init__(self, root: Path):
        self.root = root
        self.data = json.loads((root / "project.json").read_text(encoding="utf-8"))
        self._mask_cache = MaskCache()
        self._lock = threading.Lock()
        self.mtime = (root / "project.json").stat().st_mtime_ns   # 自己最后一次读/写时的修改时间
        self._migrate()

    def _fix_frame_count(self):
        """早期版本抽预览帧时没加 -fps_mode passthrough，ffmpeg 会插重复帧（比如 150 帧变 153 帧）。
        遮罩来自 ComfyUI（真实的 150 帧），预览帧却多了几帧，于是画面和遮罩逐渐错开，快速动作时轮廓明显脱离物体。
        这里按真实帧数重抽预览帧，遮罩 / 透明度截到真实帧数，删掉按旧预览帧做的图层缓存。"""
        if self.data.get("frames_ok"):
            return
        real = len(read_video(self.video("v0"))[0])
        if real != self.data["n"]:
            old = self.data["n"]
            for v in self.data["versions"]:
                d = self.video_dir(v["id"]) / "proxy"
                if d.exists():
                    shutil.rmtree(d)
                if self.video(v["id"]).exists():
                    make_proxy(self.video(v["id"]), d)
            fixed = set()
            for o in self.data["objects"]:
                if o["mask"] not in fixed:
                    m = self.raw_masks(o["id"])[:real]
                    np.savez_compressed(self.root / "masks" / f"{o['mask']}.npz", shape=np.array(m.shape), bits=np.packbits(m))
                    self._mask_cache[o["mask"]] = m
                    fixed.add(o["mask"])
                if o.get("alpha"):
                    f = self.root / "masks" / f"alpha_{o['alpha']}.npz"
                    if f.exists():
                        np.savez_compressed(f, alpha=np.load(f)["alpha"][:real])
                o["bboxes"] = frame_bboxes(self.masks(o["id"]))
                o["end"] = min(o["end"], real - 1)
                o["start"] = min(o["start"], o["end"])
            if self.cache_dir().exists():
                shutil.rmtree(self.cache_dir(), ignore_errors=True)
            self.data["n"] = real                        # 全景图本来就是按真实帧算的，不用动
            self.data["rev"] = uuid.uuid4().hex[:6]       # 前端图片地址带上它，浏览器不会再用旧的预览帧缓存
            print(f"[修复] 项目 {self.data['id']}：帧数 {old} → {real}，已重抽预览帧")
        self.data["frames_ok"] = True
        self.save()

    def _migrate_layout(self):
        """老项目（layout 没有 / =2）的文件夹迁到 layout 3：文件夹名中英连写、结果提到项目根。
        只搬移、不改内容：能硬链接的就硬链接（结果视频 / 选用的图片），不占空间。"""
        lay = self.data.get("layout")
        if lay == 3:
            return False
        root = self.root
        vold = root / ("版本" if lay == 2 else "versions")
        print(f"[迁移] {self.data.get('id')}：文件夹结构 {lay} → 3")
        for v in self.data.get("versions", []):                       # 视频 + 预览帧
            vid = v["id"]
            src = (vold / vid / "视频") if lay == 2 else (vold / vid)
            _merge_move(src, root / "版本version" / vid / "视频video")
        for r in self.data.get("results", []):                        # 结果视频：先搬（老项目的结果指向 passes/）
            base, k = r.get("base") or "v0", r.get("k")
            src = root / r["file"] if r.get("file") else None
            if src is None or not src.exists():
                cand = vold / base / "结果" / "视频" / f"视频_{k}.mp4"
                src = cand if cand.exists() else None
            if src is None:
                print(f"  结果 {k}：找不到文件，跳过")
                continue
            dst = root / "结果result" / "视频video" / f"视频_{k}_{base}.mp4"
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst.unlink()
            try:
                dst.hardlink_to(src)
            except OSError:
                shutil.copy2(src, dst)
            r["file"] = str(dst.relative_to(root)).replace("\\", "/")
        for v in self.data.get("versions", []):                       # 选用的那张（硬链接）
            vid = v["id"]
            rimg = vold / vid / "结果" / "图片"
            if rimg.exists():
                for f in rimg.glob("*.png"):
                    t = root / "结果result" / "图片images" / f"{f.stem}_{vid}{f.suffix}"
                    t.parent.mkdir(parents=True, exist_ok=True)
                    if not t.exists():
                        try:
                            t.hardlink_to(f)
                        except OSError:
                            shutil.copy2(f, t)
            if lay == 2:
                self.save()                                           # 结果的新路径先存盘，再删旧文件夹（中途出错不会指向已删的文件）
                shutil.rmtree(vold / vid / "结果", ignore_errors=True)
        if lay == 2:                                                  # 版本里的中间文件
            for v in self.data.get("versions", []):
                mid = vold / v["id"] / "中间文件"
                if mid.exists():
                    _merge_move(mid / "生成", root / "中间文件work" / "生成gen")
                    _merge_move(mid / "图片", root / "中间文件work" / "图片images")
                    shutil.rmtree(mid, ignore_errors=True)
        _merge_move(root / "passes", root / "中间文件work" / "生成gen")      # 旧结构
        _merge_move(root / "images", root / "中间文件work" / "图片images")
        _merge_move(root / "图片", root / "中间文件work" / "图片images")
        _merge_move(root / ("背景图" if lay == 2 else "panorama"), root / "背景图bg")
        _merge_move(root / ("参考图上传" if lay == 2 else "gen_refs"), root / "参考图refs")
        _merge_move(root / ("缓存" if lay == 2 else "cache"), root / "缓存cache")
        for d in (vold, root / "passes", root / "images", root / "图片", root / "背景图", root / "参考图上传",
                  root / "缓存", root / "cache", root / "panorama", root / "gen_refs"):
            _rmdir_empty(d)                                             # 空壳删掉；还有东西就先留着
        g = root / "中间文件work" / "生成gen"
        nums = [int(x.name) for x in g.iterdir() if x.is_dir() and x.name.isdigit()] if g.exists() else []
        self.data["pass_n"] = max(int(self.data.get("pass_n") or 0), (max(nums) + 1) if nums else 0)
        self.data["layout"] = 3
        self.save()
        return True

    def _migrate(self):
        """老项目补上图层属性和逐帧外框；修正早期版本的帧数错误；文件夹结构迁到最新的。"""
        self._migrate_layout()
        self._fix_frame_count()
        changed = False
        for k, o in enumerate(self.data["objects"]):
            for key, val in LAYER_DEFAULTS.items():
                if key not in o:
                    o[key] = json.loads(json.dumps(val))
                    changed = True
            if o.get("z") is None:
                o["z"] = k
                changed = True
            if "bboxes" not in o:
                o["bboxes"] = frame_bboxes(self.masks(o["id"]))
                changed = True
        for parent in self.data["objects"]:
            for part in parent.get("parts", []):
                if "parent_id" not in part:
                    self.init_part(parent, part)
                    changed = True
        old_img = {"img_close": 100, "img_grow": 30, "img_edge": 14, "img_mgrow": 16}   # 2026-11-03 前的补图范围默认值
        for it in self.data.get("images", []):
            if it.get("img_opts_v", 1) < 2:
                if all(it.get(k, v) == v for k, v in old_img.items()):   # 四项都是老默认 = 用户没调过 → 清掉，用新默认 0
                    for k in old_img:
                        it.pop(k, None)
                it["img_opts_v"] = 2
                changed = True
        if any(o.get("parts") for o in self.data["objects"]) and not self.data.get("parts_pixels"):
            self.data["parts_pixels"] = True
            self.data["rev"] = uuid.uuid4().hex[:6]
            changed = True
        if changed:
            self.save()

    @classmethod
    def create(cls, projects_dir: Path, video_path: str):
        src = Path(video_path)
        if not src.exists():
            raise FileNotFoundError(video_path)
        pid = time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        root = projects_dir / pid
        root.mkdir(parents=True)
        w0, h0, _ = probe(src)
        lim = int(WORK_LONG["v"] or 0)
        work = root / "_work.mp4" if lim and max(w0, h0) > lim else None
        if work:                                         # 分辨率太高：先缩到工作分辨率（长边 = lim，偶数），后面全用它；原片留在项目文件夹
            k = lim / max(w0, h0)
            ww, hh = max(2, round(w0 * k / 2) * 2), max(2, round(h0 * k / 2) * 2)
            subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a?", "-fps_mode", "passthrough",
                            "-vf", f"scale={ww}:{hh}:flags=lanczos", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "12",
                            "-c:a", "copy", str(work)], capture_output=True, check=True)
        frames, fps = read_video(work or src)
        n, h, w = frames.shape[:3]
        data = dict(id=pid, name=src.stem, source=str(src), w=w, h=h, fps=fps, n=n,
                    **({"source_size": [w0, h0]} if work else {}),   # 缩过：原片的尺寸（界面提示用）
                    versions=[], objects=[], current=None, color_i=0,
                    frames_ok=True,                      # 新项目的帧数本来就对；不标的话初始化时会去读还没拷进来的视频
                    layout=LAYOUT)                       # 文件夹结构（见 Project 的路径方法）；旧项目没有这个字段 = 旧结构
        (root / "project.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        p = cls(root)
        vd = p.video_dir("v0")
        vd.mkdir(parents=True)
        if work:
            shutil.move(str(work), str(vd / "video.mp4"))
        elif src.suffix.lower() == ".mp4":
            shutil.copy2(src, vd / "video.mp4")
        else:
            write_video(vd / "video.mp4", frames, fps, audio_from=src)
        if src.suffix.lower() == ".mp4" and not work:
            expose_original(root, p.video("v0"))
        else:
            shutil.copy2(src, root / ("原视频" + src.suffix.lower()))   # 缩过的项目：原片原样留一份
        make_proxy(vd / "video.mp4", vd / "proxy")
        p.data["versions"].append(dict(id="v0", label="原片", parent=None, op=None))
        p.data["current"] = "v0"
        p.save()
        return p

    def trim_copy(self, projects_dir: Path, a: int, b: int, log=print):
        """把第 a–b 帧（含）另存成一个新项目（时间轴「切出选区」）。原项目不动；当前版本的图层、画幅按新帧号带过去（_carry_layers）。
        从项目里的原视频按帧号剪（缩过的项目原视频更清楚，帧号和工作视频一一对应）；帧数对不上就用 v0 工作视频。返回新项目。"""
        n = int(self.data["n"])
        a, b = max(0, int(a)), min(n - 1, int(b))
        if a > b:
            raise ValueError("选区是空的")
        if a == 0 and b == n - 1:
            raise ValueError("选区就是整段视频，不用切")
        src = next(iter(sorted(self.root.glob("原视频.*"))), None)
        if src is None or probe(src)[2] != n:
            src = self.video("v0")
        fps = float(self.data["fps"])
        k = b - a + 1
        log(f"切出第 {a}–{b} 帧（{k} 帧，{k / fps:.2f} 秒）")
        has_audio = bool(subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                                         "-of", "csv=p=0", str(src)], capture_output=True).stdout.strip())
        fc = f"[0:v]trim=start_frame={a}:end_frame={b + 1},setpts=PTS-STARTPTS[v]"
        maps = ["-map", "[v]"]
        if has_audio:
            fc += f";[0:a]atrim=start={a / fps:.6f}:end={(b + 1) / fps:.6f},asetpts=PTS-STARTPTS[a]"
            maps += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
        tmp = self.cache_dir() / f"trim_{a}_{b}.mp4"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(src), "-filter_complex", fc, *maps, "-fps_mode", "passthrough",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "12", str(tmp)], capture_output=True)
        if r.returncode:
            raise RuntimeError("剪视频失败：" + r.stderr.decode("utf-8", "replace")[-300:])
        try:
            log("建新项目（预览帧…）")
            q = Project.create(projects_dir, str(tmp))
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        q.data["name"] = f"{self.data.get('name', '')} · 第{a}-{b}帧"
        q.data["trimmed_from"] = {"id": self.data["id"], "start": a, "end": b, "version": self.data.get("current")}
        if (q.data["n"], q.data["w"], q.data["h"]) != (k, self.data["w"], self.data["h"]):
            log(f"注意：新项目是 {q.data['w']}×{q.data['h']}、{q.data['n']} 帧，和原项目对不上，图层和画幅没带过来，要重新识别")
        else:
            self._carry_layers(q, a, b, log)
        q.save()
        return q

    def _carry_layers(self, q, a, b, log=print):
        """切出选区时把当前版本的编辑带进新项目（新项目只有 v0）：画幅、边界融合设置、图层（SAM 遮罩 / 柔边 / 逐帧修整按帧切、
        变换和蒙版设置照搬、帧范围 / 扩绘区 / 局部保留的帧号减 a）。完全落在选区外的图层不带。"""
        import copy
        vid = self.data.get("current") or "v0"
        k = b - a + 1
        md = q.root / "masks"
        md.mkdir(exist_ok=True)
        done = set()

        def rng(s, e):                                     # 原帧范围 → 新帧范围；不重叠 = None
            s, e = int(s), int(e)
            if e < a or s > b:
                return None
            return max(s, a) - a, min(e, b) - a

        def carry_files(o):
            mid = o.get("mask")
            if mid and mid not in done:
                m = self.raw_masks_by_id(mid)[a:b + 1]
                np.savez_compressed(md / f"{mid}.npz", shape=np.array(m.shape), bits=np.packbits(m))
                done.add(mid)
            aid = o.get("alpha")
            if aid and "alpha_" + aid not in done:
                f = self.root / "masks" / f"alpha_{aid}.npz"
                if f.exists():
                    np.savez_compressed(md / f"alpha_{aid}.npz", alpha=np.load(f)["alpha"][a:b + 1])
                done.add("alpha_" + aid)
            eid = o.get("edits")
            if eid and "edits_" + eid not in done:
                f = self.root / "masks" / f"edits_{eid}.npz"
                if f.exists():
                    with np.load(f) as z:
                        out = {f"{c}{int(x[1:]) - a}": z[x] for x in z.files for c in [x[0]] if a <= int(x[1:]) <= b}
                    np.savez_compressed(md / f"edits_{eid}.npz", **out)
                done.add("edits_" + eid)
            if o.get("medits"):
                o["medits"] = {str(int(i) - a): v for i, v in o["medits"].items() if a <= int(i) <= b}
            if o.get("bboxes"):
                o["bboxes"] = o["bboxes"][a:b + 1]
                bs = [x for x in o["bboxes"] if x]
                if bs and o.get("bbox"):                   # 整段外框（= 变换中心）换成选区里的；平移补回，画面不变：t' = t + (I − A)(c − c')
                    from regions import is_identity, layer_matrix
                    nb = [min(x[0] for x in bs), min(x[1] for x in bs), max(x[2] for x in bs), max(x[3] for x in bs)]
                    tf = o.get("tf") or {}
                    if not is_identity(tf):
                        A = layer_matrix(dict(tf=tf, bbox=[0, 0, 0, 0]))[:, :2].astype(float)
                        c = np.array([(o["bbox"][0] + o["bbox"][2]) / 2, (o["bbox"][1] + o["bbox"][3]) / 2])
                        c2 = np.array([(nb[0] + nb[2]) / 2, (nb[1] + nb[3]) / 2])
                        dx, dy = (np.eye(2) - A) @ (c - c2)
                        o["tf"] = dict(tf, x=float(tf.get("x", 0) + dx), y=float(tf.get("y", 0) + dy))
                    o["bbox"] = [int(v) for v in nb]
            for km in o.get("keep_masks") or []:
                f = self.root / "masks" / f"keep_{km['id']}.png"
                if f.exists():
                    shutil.copy2(f, md / f.name)
            keeps = []
            for km in o.get("keep_masks") or []:
                r = rng(km.get("start", 0), km.get("end", self.data["n"] - 1))
                if r:
                    km["start"], km["end"] = r
                    km["frame"] = min(max(int(km.get("frame", a)), a), b) - a
                    keeps.append(km)
            if "keep_masks" in o:
                o["keep_masks"] = keeps
            exts = []
            for em in o.get("ext_masks") or []:
                r = rng(em.get("start", 0), em.get("end", self.data["n"] - 1))
                if r:
                    em["start"], em["end"] = r
                    exts.append(em)
            if "ext_masks" in o:
                o["ext_masks"] = exts

        objs = []
        for o in self.data["objects"]:
            if o.get("version") != vid:
                continue
            r = rng(o.get("start", 0), o.get("end", self.data["n"] - 1))
            if not r:
                continue
            o = copy.deepcopy(o)
            o["version"] = "v0"
            o["start"], o["end"] = r
            carry_files(o)
            parts = []
            for part in o.get("parts", []):
                pr = rng(part.get("start", 0), part.get("end", self.data["n"] - 1))
                if pr:
                    part["version"] = "v0"
                    part["start"], part["end"] = pr
                    carry_files(part)
                    parts.append(part)
            if "parts" in o:
                o["parts"] = parts
            objs.append(o)
        q.data["objects"] = objs
        q.data["color_i"] = self.data.get("color_i", 0)
        for key in ("frame", "trans", "parts_pixels"):
            if key in self.data:
                q.data[key] = copy.deepcopy(self.data[key])
        log(f"带过来 {len(objs)} 个图层和画幅（第 {a}–{b} 帧 → 新项目第 0–{k - 1} 帧）")

    def log_action(self, act, **data):
        """操作记录（设置里可关，默认开）：项目文件夹里的 操作记录log.jsonl，一行一条，只往后加。
        存的是改了哪些字段、改之前 / 之后的值（不存像素：遮罩本来就是单独的文件）。超过 ACTION_LOG_MB 就把旧的改名留着、另起一个。
        记录失败只打印，不影响操作本身。"""
        if not ACTION_LOG["on"]:
            return
        try:
            f = self.root / ACTION_LOG_FILE
            line = json.dumps(dict(t=time.strftime("%Y-%m-%d %H:%M:%S"), act=act, version=self.data.get("current"), **data),
                              ensure_ascii=False, default=str)
            with self._lock:
                if f.exists() and f.stat().st_size > ACTION_LOG_MB * 1024 * 1024:
                    f.rename(f.with_name(f"{f.stem}_{time.strftime('%Y%m%d-%H%M%S')}{f.suffix}"))
                with f.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except Exception as e:
            print(f"  [操作记录] 写不进去：{e}", flush=True)

    def save(self):
        with self._lock:
            f = self.root / "project.json"
            write_json_atomic(f, self.data)
            self.mtime = f.stat().st_mtime_ns

    def changed_on_disk(self):
        """项目文件被别的进程改过（比如另一个服务实例）。自己保存的不算。"""
        try:
            return (self.root / "project.json").stat().st_mtime_ns != self.mtime
        except FileNotFoundError:
            return False

    def reload(self):
        with self._lock:
            f = self.root / "project.json"
            self.data = json.loads(f.read_text(encoding="utf-8"))
            self.mtime = f.stat().st_mtime_ns

    # --- 路径（项目里所有文件夹都从这里取）---
    # 旧结构（没有 layout 字段）：versions/vN/{video.mp4, proxy/}、passes/NNN/、images/<id>/、panorama/、gen_refs/、cache/
    # layout = 2（2026-11-12 之后建的）：版本/vN/视频、版本/vN/结果/视频、版本/vN/中间文件/{生成,图片}
    # layout = 3（现在，文件夹名中英连写；打开老项目时自动迁过来）：
    #   版本version/vN/视频video/{video.mp4, proxy/}   这个版本的视频和预览帧
    #   中间文件work/生成gen/NNN/                     一次生成 / 识别的全部过程文件（编号全项目连续：结果 id = 文件夹名）
    #   中间文件work/图片images/<标签 id>/            图片标签的底图、全部候选
    #   结果result/<vN>/视频video/项目名_vN_K.mp4     生成结果（硬链接到中间文件里的 result.mp4；K 每个版本从 1 数）
    #   结果result/<vN>/图片images/项目名_vN_首帧_K.png  每张候选一份（硬链接）；项目名_vN_首帧_选用.png = 选用的那张
    #     （2026-11-15 用户定：不同版本的结果分开、文件名 = 项目名_版本_编号；之前生成的还在 结果result/视频video、结果result/图片images，不动）
    #   背景图bg/、参考图refs/、缓存cache/、masks/     全项目共用
    @property
    def v3(self):
        return self.data.get("layout") == 3

    @property
    def v2(self):
        return self.data.get("layout") == 2

    def version_dir(self, vid):
        if self.v3:
            return self.root / "版本version" / vid
        return self.root / ("版本" if self.v2 else "versions") / vid

    def video_dir(self, vid):
        if self.v3:
            return self.version_dir(vid) / "视频video"
        return self.version_dir(vid) / "视频" if self.v2 else self.version_dir(vid)

    def video(self, vid):
        return self.video_dir(vid) / "video.mp4"

    def cache_dir(self):
        if self.v3:
            return self.root / "缓存cache"
        return self.root / ("缓存" if self.v2 else "cache")

    def panorama_dir(self):
        if self.v3:
            return self.root / "背景图bg"
        return self.root / ("背景图" if self.v2 else "panorama")

    def gen_ref_dir(self):
        if self.v3:
            return self.root / "参考图refs"
        return self.root / ("参考图上传" if self.v2 else "gen_refs")

    def gen_dir(self, k=None):
        """一次生成的工作文件夹：中间文件work/生成gen/NNN（编号全项目连续）。"""
        d = self.root / "中间文件work" / "生成gen" if self.v3 else (
            self.root / "passes" if not self.v2 else self.version_dir(self.data["current"]) / "中间文件" / "生成")
        return d / f"{k:03d}" if k is not None else d

    def dedupe_pass(self, k, look_back=40, min_size=DEDUPE_MIN):
        """这一 pass 里和以前某 pass 完全相同的文件换成硬链接：每个 pass 还是一份，但不占空间。
        只认同名、同大小、md5 也一样的（大小先挡一道，避免每次生成都扫全场）。"""
        d, targets, k = self.gen_dir(int(k)), [], int(k)
        if not d.is_dir():
            return 0
        for i in range(k - 1, max(-1, k - look_back) - 1, -1):
            p = self.gen_dir(i)
            if p.is_dir():
                targets.append(p)
        if not targets:
            return 0
        n = 0
        for f in d.iterdir():
            if not f.is_file():
                continue
            try:
                size = f.stat().st_size
            except OSError:
                continue
            if size < min_size:
                continue
            for pd in targets:
                g = pd / f.name
                if not g.is_file() or not same_bytes(f, g):
                    continue
                try:
                    f.unlink()
                    f.hardlink_to(g)
                    n += 1
                except OSError:
                    pass
                break
        return n

    def cache_bytes(self):
        """缓存文件夹占多少（字节）：设置里「清理缓存」显示用。"""
        n = 0
        d = self.cache_dir()
        if d.is_dir():
            for f in d.rglob("*"):
                try:
                    if f.is_file():
                        n += f.stat().st_size
                except OSError:
                    pass
        return n

    def clear_cache(self):
        """删掉缓存文件夹里的内容（抠图 PNG、分区 npy、缩略图）；返回释放的字节数。会重新算，不影响项目和结果。"""
        d, freed = self.cache_dir(), 0
        if not d.is_dir():
            return 0
        for f in d.rglob("*"):
            try:
                if f.is_file():
                    freed += f.stat().st_size
                    f.unlink()
            except OSError:
                pass
        for sub in sorted((x for x in d.rglob("*") if x.is_dir()), key=lambda x: -len(x.parts)):
            try:
                sub.rmdir()
            except OSError:
                pass
        return freed

    def image_dir(self, it):
        """图片标签的文件夹：中间文件work/图片images/<id>（全项目一处，不再按版本分）。"""
        if self.v3:
            return self.root / "中间文件work" / "图片images" / it["id"]
        if not self.v2:
            return self.root / "images" / it["id"]
        v = it.get("version")
        return self.version_dir(v) / "中间文件" / "图片" / it["id"] if v else self.root / "图片" / it["id"]

    def out_name(self, vid, *parts):
        """结果文件名：项目名_版本_…（项目名里不能进文件名的字符换成 _）。"""
        import re
        name = re.sub(r'[\\/:*?"<>|]', "_", str(self.data.get("name") or self.data["id"])).replace(" · ", "_").strip()[:60]
        return "_".join([name or self.data["id"], vid, *map(str, parts)])

    def version_results_dir(self, vid, kind):
        """这个版本的结果：结果result/<vN>/视频video 或 结果result/<vN>/图片images（只有 layout 3）。"""
        return self.root / "结果result" / vid / kind if self.v3 else None

    def results_dir(self, kind):
        """结果文件夹：结果result/视频video 或 结果result/图片images（项目根，不按版本分；文件名带版本）。"""
        if not self.v3:
            return None
        return self.root / "结果result" / kind

    # --- 版本 ---

    def delete_version(self, vid):
        versions = self.data['versions']
        if not any(v['id'] == vid for v in versions):
            raise ValueError('视频版本不存在')
        if vid == 'v0':
            raise ValueError('原片是对比和音频来源，不能删除')
        if any(v.get('parent') == vid for v in versions):
            raise ValueError('其他视频版本依赖此版本，请先删除其后续版本')
        if (self.data.get('panorama') or {}).get('version') == vid:
            raise ValueError('背景全景图仍引用此版本，请先处理背景图')
        target = self.version_dir(vid).resolve()
        parent = self.version_dir(vid).parent.resolve()
        if target.parent != parent or target == parent:
            raise ValueError('版本路径不合法')
        for o in [v for v in self.objects_in(vid) if not v.get("parent_id")]:
            self.delete_object(o['id'])
        self.data['versions'] = [v for v in versions if v['id'] != vid]
        # 这个版本的图片标签和视频结果条目一起去掉（文件留在 images/、passes/ 里）
        self.data['images'] = [it for it in self.data.get('images', []) if it.get('version') != vid or it['kind'] == 'bg']
        self.data['results'] = [r for r in self.data.get('results', []) if r.get('base') != vid]
        if self.data['current'] == vid:
            self.data['current'] = 'v0'
        self.save()                                      # 先存再删文件夹：删到一半出错（Windows 文件被占用）也不会留下指向半截文件夹的版本
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)

    def add_version(self, frames, label, parent, op):
        k = len(self.data["versions"])
        while self.version_dir(f"v{k}").exists():        # 跳过以前失败留下的目录
            k += 1
        vid = f"v{k}"
        tmp = self.version_dir(f"_tmp_{vid}")             # 先写临时目录，全部成功再改名
        shutil.rmtree(tmp, ignore_errors=True)
        tv = tmp / ("视频video" if self.v3 else "视频") if (self.v3 or self.v2) else tmp
        tv.mkdir(parents=True)
        write_video(tv / "video.mp4", frames, self.data["fps"], audio_from=self.video("v0"))
        make_proxy(tv / "video.mp4", tv / "proxy")
        tmp.rename(self.version_dir(vid))
        self.data["versions"].append(dict(id=vid, label=label, parent=parent, op=op, time=time.strftime("%m-%d %H:%M")))
        self.data["current"] = vid
        self.save()
        return vid

    def duplicate_version(self, src, label=None, layers=True):
        """复制版本：同一段视频（视频和预览帧用硬链接，不重新编码、不占空间），图层和编辑原样复制一份（新 id），
        另起一个版本试别的改法，原来的不动。遮罩 / 柔边 / 手动修改文件两个版本共用：改手动修改时 set_frame_edits 会另存一份，
        删图层 / 删版本时 delete_object 只删没人用的文件。画框、裁切黑边、过渡等是项目级设置，两个版本共用。
        layers=False：只要视频、不带图层（「新建原视频版本」= 从 v0 重新开始）。"""
        sv = next((v for v in self.data["versions"] if v["id"] == src), None)
        if sv is None:
            raise ValueError("视频版本不存在")
        k = len(self.data["versions"])
        while self.version_dir(f"v{k}").exists() or any(v["id"] == f"v{k}" for v in self.data["versions"]):
            k += 1
        vid = f"v{k}"
        tmp = self.version_dir(f"_tmp_{vid}")
        shutil.rmtree(tmp, ignore_errors=True)
        sd = self.video_dir(src)                         # 只要视频和预览帧（新结构里版本文件夹还有结果 / 中间文件，不复制）
        tv = tmp / ("视频video" if self.v3 else "视频") if (self.v3 or self.v2) else tmp
        for f in sd.rglob("*"):                          # video.mp4 + proxy*/ 帧：硬链接，失败就复制
            if f.is_file():
                t = tv / f.relative_to(sd)
                t.parent.mkdir(parents=True, exist_ok=True)
                try:
                    t.hardlink_to(f)
                except OSError:
                    shutil.copy2(f, t)
        tmp.rename(self.version_dir(vid))
        ids = {}
        new = lambda old: ids.setdefault(old, uuid.uuid4().hex[:8])
        for o in [o for o in self.data["objects"] if layers and o["version"] == src]:
            c = json.loads(json.dumps(o))
            c["id"], c["version"] = new(o["id"]), vid
            for part in c.get("parts", []):
                part["id"] = new(part["id"])
                part["version"] = vid
                if part.get("parent_id"):
                    part["parent_id"] = c["id"]
            self.data["objects"].append(c)
        if layers:
            self._copy_image_tabs(src, vid, ids)
        self.data["versions"].append(dict(id=vid, label=label or (f"{sv.get('label') or src} 副本" if layers else "原视频（新）"), parent=src,
                                          op={"type": "duplicate" if layers else "fresh"}, time=time.strftime("%m-%d %H:%M")))
        self.data["current"] = vid
        self.save()
        return vid

    def _copy_image_tabs(self, src, vid, ids):
        """复制版本时：src 版本的首帧 / 尾帧 / 资料卡标签复制一份给 vid（新 id，图片文件夹硬链接），挑好的那张照样能用。
        签名里的版本号和图层 id 换成新的，不然新版本会把它们判成「图层改过、过期」。"""
        import images as IM

        def swap(s):                                     # 换 id 后按新 id 重新排序（layer_sig 按图层 id 排）
            if not isinstance(s, str):
                return s
            s = self._swap_ids(s, src, vid, ids)
            try:
                v = json.loads(s)
                return json.dumps(v[:2] + sorted(v[2:], key=lambda x: x[0]), sort_keys=True)
            except (ValueError, TypeError, IndexError, KeyError):
                return s
        bkey = lambda v, it: json.dumps([IM.edit_sig(self, v), it.get("layer_opts") or {}], sort_keys=True)   # = refedit.base_key
        for it in [it for it in self.data.get("images", []) if it.get("version") == src and it["kind"] in ("first", "last", "key", "card")]:
            c = json.loads(json.dumps(it))
            c["id"], c["version"] = uuid.uuid4().hex[:8], vid
            if c.get("oid"):
                c["oid"] = ids.get(c["oid"], c["oid"])
            c["sig"] = swap(c.get("sig"))
            if c.get("cand_sig"):
                c["cand_sig"] = {n: swap(s) for n, s in c["cand_sig"].items()}
            if c.get("layer_opts"):
                c["layer_opts"] = {ids.get(k, k): v for k, v in c["layer_opts"].items()}
            sd, td = self.image_dir(it), self.image_dir(c)
            for f in (sd.rglob("*") if sd.exists() else []):
                if f.is_file():
                    t = td / f.relative_to(sd)
                    t.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        t.hardlink_to(f)
                    except OSError:
                        shutil.copy2(f, t)
            if c.get("base_key"):                         # 原版本的画面是按现在的编辑取的，副本也算是（编辑一模一样）；原来就过期的照样过期
                c["base_key"] = bkey(vid, {**c}) if it["base_key"] == bkey(src, it) else None
            self.data["images"].append(c)

    @staticmethod
    def _swap_ids(s, src, vid, ids):
        for old, new in ids.items():                     # 图层 id 是随机 8 位十六进制，直接换子串（base_key 里是转义过的 JSON，带不带引号都要换到）
            s = s.replace(old, new)
        return s.replace(f'["{src}", ', f'["{vid}", ').replace(f'[\\"{src}\\", ', f'[\\"{vid}\\", ')

    # --- 物体 ---
    def next_color(self):
        c = PALETTE[self.data["color_i"] % len(PALETTE)]
        self.data["color_i"] += 1
        return c

    def save_masks(self, masks):
        mid = uuid.uuid4().hex[:10]
        (self.root / "masks").mkdir(exist_ok=True)
        np.savez_compressed(self.root / "masks" / f"{mid}.npz", shape=np.array(masks.shape), bits=np.packbits(masks))
        return mid

    def add_object(self, name, masks, version, color=None, prompt=None):
        idx = np.nonzero(masks.reshape(len(masks), -1).any(1))[0]
        if not len(idx):
            return None
        ys, xs = np.nonzero(masks.any(0))
        zs = [o.get("z") or 0 for o in self.objects_in(version)]
        obj = dict(id=uuid.uuid4().hex[:8], name=name, version=version, mask=self.save_masks(masks),
                   color=color or self.next_color(), start=int(idx[0]), end=int(idx[-1]), prompt=prompt,
                   bbox=[int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())], bboxes=frame_bboxes(masks),
                   **{**json.loads(json.dumps(LAYER_DEFAULTS)), "grow": NEW_LAYER_GROW}, **SUBJECT_MASK_DEFAULTS)
        obj["z"] = max(zs, default=-1) + 1                  # 新物体放在最上层
        self.data["objects"].append(obj)
        self.save()
        return obj

    def delete_object(self, oid):
        """Delete only this object's unshared recognition assets and preview cache."""
        removed = self.obj(oid)
        if removed.get("parent_id"):
            self.obj(removed["parent_id"])["parts"].remove(removed)
            self._mask_cache.clear()
            self.save()
            return
        remaining = [o for o in self.data['objects'] if o['id'] != oid]
        root = self.root.resolve()
        targets = []
        for part in removed.get("parts", []):
            if not any(part["mask"] == other["mask"] for o in remaining for other in o.get("parts", [])):
                targets.append(root / "masks" / f'{part["mask"]}.npz')
        for field, prefix in [('mask', ''), ('alpha', 'alpha_'), ('edits', 'edits_')]:
            value = removed.get(field)
            if value and not any(o.get(field) == value for o in remaining):
                targets.append(root / 'masks' / f'{prefix}{value}.npz')
        cache = root / self.cache_dir().name
        if cache.exists():
            targets.extend(p for p in cache.iterdir() if p.name.startswith(f'layer_{oid}_'))
        # Validate every absolute target before any removal, including symlinks.
        for p in targets:
            resolved = p.resolve()
            expected = cache if p.parent == cache else root / 'masks'
            if not resolved.is_relative_to(expected) or resolved == expected:
                raise ValueError('拒绝清理项目目录外的文件')
        # 先把图层从项目里去掉并存盘，再删文件：删文件失败（Windows 上缓存图被占用）也不会留下没有遮罩的坏图层
        self.data['objects'] = remaining
        self._mask_cache.clear()
        self.save()
        for p in targets:
            try:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
            except OSError as e:
                print(f"删图层文件失败（留着不影响）：{p.name}：{e}")

    def init_part(self, parent, part):
        part.update({k: json.loads(json.dumps(v)) for k, v in LAYER_DEFAULTS.items() if k not in part})
        part.update(parent_id=parent["id"], version=parent["version"], color=parent["color"],
                    visible=part.pop("enabled", True), start=parent["start"], end=parent["end"])
        m = self.part_masks(part)
        ys, xs = np.nonzero(m.any(0))
        part["bbox"] = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else parent["bbox"]
        part["bboxes"] = frame_bboxes(m)

    def obj(self, oid):
        for o in self.data["objects"]:
            if o["id"] == oid:
                return o
        for parent in self.data["objects"]:
            for part in parent.get("parts", []):
                if part["id"] == oid:
                    return part
        raise KeyError(oid)

    def raw_masks(self, oid):
        """SAM 3.1 的原始遮罩（不含手动修改）。"""
        return self.raw_masks_by_id(self.obj(oid)["mask"])

    def raw_masks_by_id(self, mid):
        """masks/<mid>.npz 解包成 (帧, 高, 宽) bool，有缓存。"""
        if mid not in self._mask_cache:
            z = np.load(self.root / "masks" / f"{mid}.npz")
            shape = tuple(z["shape"])
            self._mask_cache[mid] = np.unpackbits(z["bits"], count=int(np.prod(shape))).reshape(shape).astype(bool)
        return self._mask_cache[mid]

    def grown_masks(self, oid, grow=None):
        """SAM 原始结果按图层的“边缘外扩”扩 / 收几个像素（不含手动修改）。"""
        o = self.obj(oid)
        g = int(o.get("grow", 0) if grow is None else grow)
        c = clean_opts(o)
        base = self.raw_masks(oid)
        if any(c):                                       # 先清理（补小孔 / 去碎块 / 时间平滑），再扩 / 收
            ck = f"{o['mask']}|c{c}"
            if ck not in self._mask_cache:
                self._mask_cache[ck] = clean_masks(base, *c)
            base = self._mask_cache[ck]
        if not g:
            return base
        key = f"{o['mask']}|c{c}|g{g}" if any(c) else f"{o['mask']}|g{g}"
        if key not in self._mask_cache:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * abs(g) + 1,) * 2)
            op = cv2.dilate if g > 0 else cv2.erode
            self._mask_cache[key] = np.stack([op(m.astype(np.uint8), k) > 0 if m.any() else m for m in base])
        return self._mask_cache[key]

    def crop_rows(self):
        """画框「裁切黑边」后原片里还算画面的行 (上, 下)；没裁切黑边 = None。黑边里的东西一律不属于任何图层。"""
        c = (self.data.get("frame") or {}).get("crop")
        return (int(c["t"]), self.data["h"] - int(c["b"])) if c and (c.get("t") or c.get("b")) else None

    def masks(self, oid, grow=None, edits=True):
        """裁切黑边后：黑边里的部分去掉（外扩、手动补都伸不进黑边）。"""
        m = self._masks(oid, grow, edits)
        children = self.obj(oid).get("parts", [])
        if children:
            masks = [self._masks(child["id"], None, True) for child in children]
            key = ("split", oid, grow, edits)
            signature = (id(m), tuple(id(v) for v in masks))
            hit = self._mask_cache.get(key)
            if hit is None or hit[0] != signature:
                separated = m.copy()
                for child_mask in masks:
                    separated &= ~child_mask
                hit = self._mask_cache[key] = (signature, separated)
            m = hit[1]
        cr = self.crop_rows()
        if not cr:
            return m
        key = ("crop", oid, edits, grow)                 # 每个图层每种外扩只留一份（整段遮罩很大），底下的遮罩或黑边变了就重算
        hit = self._mask_cache.get(key)
        if hit is None or hit[0] is not m or hit[1] != cr:
            c = m.copy()
            c[:, :cr[0]] = False
            c[:, cr[1]:] = False
            hit = self._mask_cache[key] = (m, cr, c)
        return hit[2]

    def part_masks(self, part):
        mid = part["mask"]
        if mid not in self._mask_cache:
            with np.load(self.root / "masks" / f"{mid}.npz") as z:
                shape = tuple(z["shape"])
                self._mask_cache[mid] = np.unpackbits(z["bits"], count=int(np.prod(shape))).reshape(shape).astype(bool)
        return self._mask_cache[mid]

    def _masks(self, oid, grow=None, edits=True):
        """实际使用的遮罩 = SAM 原始结果 → 边缘外扩 → 手动修改（补上的加进来，擦掉的去掉）。生成、预览、点选都用它。"""
        o = self.obj(oid)
        base = self.grown_masks(oid, grow)
        if not edits or not o.get("edits"):
            return base
        g = int(o.get("grow", 0) if grow is None else grow)
        key = f"{o['mask']}|c{clean_opts(o)}|g{g}|{o['edits']}|{sum(o.get('medits', {}).values())}"
        if key not in self._mask_cache:
            m = base.copy()
            for i, (add, erase) in self._edits(o).items():
                m[i] = (m[i] | add) & ~erase
            self._mask_cache[key] = m
        return self._mask_cache[key]

    # --- 手动修改（每帧两张原分辨率图：补上 / 擦掉），原始的 SAM / MatAnyone 结果不动 ---
    def _edits(self, o):
        eid = o.get("edits")
        if not eid:
            return {}
        key = "edits_" + eid
        if key not in self._mask_cache:
            f = self.root / "masks" / f"{key}.npz"
            d = {}
            if f.exists():
                z = np.load(f)
                h, w = self.data["h"], self.data["w"]
                unpack = lambda b: np.unpackbits(b, count=h * w).reshape(h, w).astype(bool)
                for k in z.files:
                    if k.startswith("a"):
                        i = int(k[1:])
                        d[i] = (unpack(z[k]), unpack(z["e" + k[1:]]))
            self._mask_cache[key] = d
        return self._mask_cache[key]

    def frame_edits(self, oid, i):
        h, w = self.data["h"], self.data["w"]
        z = np.zeros((h, w), bool)
        return self._edits(self.obj(oid)).get(i, (z, z))

    def set_frame_edits(self, oid, i, add, erase):
        """保存某一帧的手动修改。和别的版本的物体共用同一份修改记录时另存一份，不影响别的版本。"""
        o = self.obj(oid)
        d = dict(self._edits(o))
        if add.any() or erase.any():
            d[i] = (add & ~erase, erase)
        else:
            d.pop(i, None)                                   # 两张都空 = 还原成原始结果
        everyone = [y for x in self.data["objects"] for y in [x, *x.get("parts", [])]]   # 子图层也算（复制版本后父子都共用文件）
        if not o.get("edits") or any(x is not o and x.get("edits") == o["edits"] for x in everyone):
            o["edits"] = uuid.uuid4().hex[:10]
        np.savez_compressed(self.root / "masks" / f"edits_{o['edits']}.npz",
                            **{f"a{k}": np.packbits(v[0]) for k, v in d.items()},
                            **{f"e{k}": np.packbits(v[1]) for k, v in d.items()})
        self._mask_cache["edits_" + o["edits"]] = d
        o.setdefault("medits", {})
        o["medits"][str(i)] = o["medits"].get(str(i), 0) + 1   # 前端图片地址带上它：只有改过的帧重新加载
        masks = self.masks(oid)
        idx = np.nonzero(masks.reshape(len(masks), -1).any(1))[0]
        if len(idx):
            o["start"], o["end"] = int(idx[0]), int(idx[-1])
            ys, xs = np.nonzero(masks.any(0))
            o["bbox"] = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
        o["bboxes"] = frame_bboxes(masks)
        self.save()
        return o

    def pw(self):
        """这个项目实际用的画布预览宽度：设置里选的，不超过原片宽，取偶数。"""
        if PREVIEW["w"] == PROXY_W:                      # 默认：和以前一样一律 960（原片比 960 窄时也放大到 960）
            return PROXY_W
        return min(PREVIEW["w"], self.data["w"]) // 2 * 2

    def proxy_size(self):
        return self.pw(), round(self.data["h"] * self.pw() / self.data["w"] / 2) * 2

    def proxy_dir(self, vid):
        """某个视频版本的预览帧文件夹（按现在的预览宽度）；没有就现做（一段视频十几秒）。"""
        w = self.pw()
        d = self.video_dir(vid) / ("proxy" if w == PROXY_W else f"proxy_{w}")
        if not (d / "00001.jpg").exists() and self.video(vid).exists():
            with _proxy_lock:
                if not (d / "00001.jpg").exists():
                    make_proxy(self.video(vid), d, w)
        return d

    def proxy_mask(self, oid, i, raw=False, grow=None):
        """预览分辨率的遮罩。raw=True：不含手动修改（外扩照常）。"""
        m = self.masks(oid, grow, edits=not raw)[i].astype(np.uint8)
        return cv2.resize(m, self.proxy_size(), interpolation=cv2.INTER_NEAREST) > 0

    # --- 半透明 alpha（MatAnyone2），按预览分辨率存，供界面拖动预览 ---
    def save_alpha(self, oid, alpha):
        small = np.stack([cv2.resize(a, (PROXY_W, round(self.data["h"] * PROXY_W / self.data["w"] / 2) * 2), interpolation=cv2.INTER_AREA)
                          for a in alpha])                 # 存的时候固定按 960 存；用的时候按现在的预览大小缩放
        aid = uuid.uuid4().hex[:10]
        np.savez_compressed(self.root / "masks" / f"alpha_{aid}.npz", alpha=small)
        self.obj(oid)["alpha"] = aid
        self.save()

    def proxy_alpha(self, oid, i, raw=False, grow=None):
        """MatAnyone2 的半透明 alpha（预览分辨率）。先按“边缘外扩”整体扩 / 收（保留柔边的形状），
        再叠手动修改：补上的地方不透明，擦掉的地方透明，没改过的地方保留原来的柔边。"""
        o = self.obj(oid)
        aid = o.get("alpha")
        if not aid:
            return None
        key = "alpha_" + aid
        if key not in self._mask_cache:
            self._mask_cache[key] = np.load(self.root / "masks" / f"{key}.npz")["alpha"]
        a = self._mask_cache[key][i]
        if a.shape[::-1] != self.proxy_size():           # 存的是 960 宽，预览宽度改过：缩放到现在的预览大小
            a = cv2.resize(a, self.proxy_size(), interpolation=cv2.INTER_LINEAR)
        g = int(o.get("grow", 0) if grow is None else grow)
        if g:
            r = max(1, round(abs(g) * a.shape[1] / self.data["w"]))   # 原画面像素 → 预览分辨率像素
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1,) * 2)
            a = (cv2.dilate if g > 0 else cv2.erode)(a, k)
        ed = None if raw else self._edits(o).get(i)
        if ed is None:
            return self._crop_small(a)
        ph, pw = a.shape
        small = lambda m: cv2.resize(m.astype(np.float32), (pw, ph), interpolation=cv2.INTER_AREA)
        add, erase = small(ed[0]), small(ed[1])
        out = np.maximum(a.astype(np.float32), add * 255) * (1 - erase)
        return self._crop_small(out.clip(0, 255).astype(np.uint8))

    def _crop_small(self, a):
        """预览分辨率的 alpha：裁切黑边后黑边里清零。"""
        cr = self.crop_rows()
        if not cr:
            return a
        s = a.shape[0] / self.data["h"]
        a = a.copy()
        a[:int(round(cr[0] * s))] = 0
        a[int(round(cr[1] * s)):] = 0
        return a

    def objects_in(self, vid):
        return [item for o in self.data["objects"] if o["version"] == vid for item in [o, *o.get("parts", [])]]

    def bgswap_on(self, vid=None):
        """这个版本有启用的「替换背景」图层（字段 bgswap）：首帧 / 尾帧、视频生成默认不带旧背景图（2026-11-18 用户定）。"""
        return any(o.get("bgswap") and not o.get("off") for o in self.objects_in(vid or self.data["current"]))

    def layer_order(self, vid):
        roots = sorted((o for o in self.data["objects"] if o["version"] == vid), key=lambda o: o.get("z") or 0)
        return [item for o in roots for item in [o, *sorted(o.get("parts", []), key=lambda v: v.get("z") or 0)]]

    def layer_matrix(self, o):
        from regions import layer_matrix
        matrix = np.vstack([layer_matrix(o), [0, 0, 1]])
        if o.get("parent_id"):
            matrix = np.vstack([self.layer_matrix(self.obj(o["parent_id"])), [0, 0, 1]]) @ matrix
        return matrix[:2].astype(np.float32)

    def layer_visible(self, o):
        return o.get("visible", True) and (not o.get("parent_id") or self.layer_visible(self.obj(o["parent_id"])))

    def frames(self, vid):
        return read_video(self.video(vid))[0]

    def frame(self, vid, i):
        """只解码第 i 帧（原分辨率）。取首帧 / 尾帧时不用把整段视频读进内存（整段要 7 秒多）。"""
        path = self.video(vid)
        w, h, _ = probe(path)
        raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{int(i)})", "-frames:v", "1",
                              "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True).stdout
        if len(raw) < w * h * 3:                         # 帧号越界之类：退回整段读
            return self.frames(vid)[i]
        return np.frombuffer(raw[:w * h * 3], np.uint8).reshape(h, w, 3).copy()

    def new_pass_dir(self, vid=None):
        """一次生成 / 识别的工作文件夹：中间文件work/生成gen/NNN（编号全项目连续，结果 id 用它，不能撞）。
        旧结构的项目还留在 passes/ 里（打开时会迁过来）。"""
        if self.v3:
            with self._lock:
                k = int(self.data.get("pass_n", 0))
                while self.gen_dir(k).exists():
                    k += 1
                self.data["pass_n"] = k + 1
            pd = self.gen_dir(k)
            pd.mkdir(parents=True)
            self.save()
            return pd
        if not self.v2:
            d = self.root / "passes"
            d.mkdir(exist_ok=True)
            k = len(list(d.iterdir()))
            pd = d / f"{k:03d}"
            pd.mkdir()
            return pd
        with self._lock:
            k = int(self.data.get("pass_n", 0))
            while any((self.version_dir(v["id"]) / "中间文件" / "生成" / f"{k:03d}").exists() for v in self.data["versions"]):
                k += 1
            self.data["pass_n"] = k + 1
        pd = self.version_dir(vid or self.data["current"]) / "中间文件" / "生成" / f"{k:03d}"
        pd.mkdir(parents=True)
        self.save()
        return pd
