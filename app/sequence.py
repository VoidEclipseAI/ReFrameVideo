"""长片段分段（2026-11-15 用户定）：长视频不整段当一个项目，而是切成几段，每段是一个普通项目（照常识别、编辑、生成），
最后按顺序拼回全片。

- 长片段 = projects/<sid>/sequence.json（不是 project.json，项目列表不会把它当普通项目）。
- 导入时整条视频只用 ffmpeg 转一份恒定帧率的工作母版 master.mp4（长边超过工作分辨率就缩）和一份小预览 preview.mp4，
  不把整条视频读进内存（长视频会占几十 GB）。帧号 = round(秒 × fps)，切点、剪段、拼接都按它算。
- 自动切：先按镜头切换（在预览上做 scene 检测），再把超过 SEG_MAX_S 秒的段平均切开。切完用户可以改。
- 每段第一次「编辑这一段」才从母版精确剪出来（trim 帧号 + atrim 秒），建成普通项目（project.json 里带 seq 字段）；
  建过项目的段切点锁住，不能再改（已做的编辑会对不上）。
- 导出全片：每段用选的结果（auto = 这一段当前版本最新的视频结果；没有就原片）或原片，缩放 + 补边到母版大小，
  补齐 / 截到这一段的帧数，按顺序拼；声音整条用母版的。导出后检查总帧数 = 母版帧数。
- 锁定（2026-11-17 用户定）：段的 lock = True 时「全片里用」定死成一条具体的结果（或原视频），之后再生成也不换；
  分段页的整条预览 / 缩略图条用 preview_mix.mp4：锁定的段换成它锁定的结果，其余照原视频（build_mix）。
"""
import json
import math
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

from project import FFMPEG, FFPROBE, WORK_LONG, Project, write_json_atomic

SEG_MAX_S = 10          # 一段最长多少秒（自动切时超过就平均切开）
ASK_S = 10              # 导入时超过多少秒问要不要分段（前端用 /api/probe_video 判断）
SCENE = 0.3             # ffmpeg scene 检测阈值
PREVIEW = {"long": 1280}          # 分段页整条预览的长边（设置「分段预览清晰度」，2026-11-18 用户定，默认 1280；原来固定 640 宽）
PREVIEW_CHOICES = (640, 960, 1280, 1920)


def preview_size(w, h):
    """预览尺寸：长边 = 设置值（母版更小就用母版大小），偶数。"""
    L = min(PREVIEW["long"], max(w, h))
    if w >= h:
        return L // 2 * 2, max(2, round(h * L / w / 2) * 2)
    return max(2, round(w * L / h / 2) * 2), L // 2 * 2


def make_preview(root: Path, w, h, log=print):
    """从母版做分段页播放用的预览（setsar=1：缩放后像素保持正方形）。换了尺寸：缩略图条、拼好的整条预览都作废。"""
    pw, ph = preview_size(w, h)
    log(f"做预览：{pw}×{ph}（分段页播放用）")
    tmp = root / "preview.tmp.mp4"
    _run([FFMPEG, "-v", "error", "-y", "-i", root / "master.mp4", "-map", "0:v:0", "-map", "0:a:0?", "-c:a", "aac", "-b:a", "96k",
          "-vf", f"scale={pw}:{ph},setsar=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "26", "-g", "12", "-f", "mp4", tmp])
    tmp.replace(root / "preview.mp4")
    for x in [*root.glob("strip_*.jpg"), root / "preview_mix.mp4"]:
        x.unlink(missing_ok=True)
    return max(pw, ph)


def preview_long(root: Path, d):
    """现在这份预览的长边（旧长片段没记过：读一次文件）。"""
    if d.get("preview_long"):
        return d["preview_long"]
    try:
        v = vprobe(root / "preview.mp4")
        return max(v["w"], v["h"])
    except Exception:
        return 0


def preview_old(root: Path, d):
    return preview_long(root, d) != max(preview_size(d["w"], d["h"]))
MIN_SEG_S = 0.5         # 镜头切点离得太近（短于这个）就不切
_lock = threading.Lock()


def _run(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[-800:])
    return r


def vprobe(path):
    """视频信息：宽、高、fps、帧数（数包，不解码）、时长、有没有声音。"""
    out = _run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets",
                "-show_entries", "stream=width,height,r_frame_rate,nb_read_packets:format=duration", "-of", "json", path]).stdout
    j = json.loads(out.decode("utf-8"))
    s = j["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    n = int(s.get("nb_read_packets") or 0)
    dur = float(j.get("format", {}).get("duration") or (n / fps if fps else 0))
    a = _run([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", path]).stdout
    return dict(w=int(s["width"]), h=int(s["height"]), fps=fps, n=n or int(round(dur * fps)), duration=dur,
                audio=bool(a.decode("utf-8", "replace").strip()))


def root_of(projects_dir: Path, sid: str) -> Path:
    if not re.fullmatch(r"seq-[0-9A-Za-z-]+", sid or ""):
        raise KeyError(sid)
    r = projects_dir / sid
    if not (r / "sequence.json").is_file():
        raise KeyError(sid)
    return r


def load(root: Path):
    return json.loads((root / "sequence.json").read_text(encoding="utf-8"))


def save(root: Path, d):
    with _lock:
        write_json_atomic(root / "sequence.json", d)


def list_all(projects_dir: Path):
    out = []
    for r in sorted(projects_dir.glob("seq-*"), reverse=True):
        if (r / "sequence.json").is_file():
            try:
                d = load(r)
            except (OSError, ValueError):
                continue
            out.append({k: d.get(k) for k in ("id", "name", "n", "fps", "w", "h")} |
                       {"segments": len(d.get("segments", [])),
                        "time": time.strftime("%m-%d %H:%M", time.localtime((r / "sequence.json").stat().st_mtime))})
    return out


def pieces(starts, n):
    """段的起点（不含 0）→ [[起, 止]]（止含在内）。"""
    b = [0] + sorted({int(s) for s in starts if 0 < int(s) < n}) + [n]
    return [[b[i], b[i + 1] - 1] for i in range(len(b) - 1)]


def auto_starts(cuts, n, fps, max_s=SEG_MAX_S):
    """镜头切点（帧号）→ 段起点：太近的不要，超过 max_s 秒的段平均切开。"""
    min_f, max_f = max(1, int(round(MIN_SEG_S * fps))), max(2, int(round(max_s * fps)))
    st = []
    for c in sorted(set(cuts)):
        if min_f <= c <= n - min_f and (not st or c - st[-1] >= min_f):
            st.append(c)
    out = []
    for a, b in pieces(st, n):
        L = b - a + 1
        k = math.ceil(L / max_f)
        out += [a + round(L * i / k) for i in range(k)]
    return [s for s in out if s > 0]


def scene_cuts(video, fps):
    """ffmpeg scene 检测：镜头切换处的帧号。"""
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(video), "-an", "-vf", f"select='gt(scene,{SCENE})',showinfo", "-f", "null", "-"],
                       capture_output=True)
    ts = re.findall(r"pts_time:([0-9.]+)", r.stderr.decode("utf-8", "replace"))
    return [int(round(float(t) * fps)) for t in ts]


def _new_id(d):
    d["seg_i"] = d.get("seg_i", 0) + 1
    return f"g{d['seg_i']}"


def set_starts(d, starts):
    """按新的段起点重建分段：已经建了项目的段必须原样保留（切点锁住）；起止没变的段保留它的 id / 设置。"""
    old = {(s["start"], s["end"]): s for s in d.get("segments", [])}
    new = pieces(starts, d["n"])
    keys = {tuple(p) for p in new}
    for s in d.get("segments", []):
        if s.get("pid") and (s["start"], s["end"]) not in keys:
            raise ValueError(f"第 {s['start']}–{s['end']} 帧那一段已经在编辑了，不能改它的切点")
    d["segments"] = [old.get(tuple(p)) or {"id": _new_id(d), "start": p[0], "end": p[1], "pid": None, "use": "auto"} for p in new]
    return d


def create(projects_dir: Path, src: str, log=print):
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(str(src))
    info = vprobe(src)
    sid = "seq-" + time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    root = projects_dir / sid
    root.mkdir(parents=True)
    fps, lim = info["fps"], int(WORK_LONG["v"] or 0)
    w0, h0 = info["w"], info["h"]
    if lim and max(w0, h0) > lim:
        k = lim / max(w0, h0)
        w, h = max(2, round(w0 * k / 2) * 2), max(2, round(h0 * k / 2) * 2)
    else:
        w, h = w0 // 2 * 2, h0 // 2 * 2
    log(f"① 转工作母版：{w0}×{h0} → {w}×{h}，恒定 {fps:.3f} fps（{info['duration']:.1f} 秒）")
    audio = ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k"]
    _run([FFMPEG, "-v", "error", "-y", "-i", src, "-map", "0:v:0", *audio, "-vf", f"scale={w}:{h}:flags=lanczos",
          "-fps_mode", "cfr", "-r", f"{fps}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "12", root / "master.mp4"])
    log("② 做预览")
    plong = make_preview(root, w, h, log)
    m = vprobe(root / "master.mp4")
    d = dict(id=sid, name=src.stem, source=str(src), w=w, h=h, fps=fps, n=m["n"], audio=m["audio"], preview_long=plong,
             **({"source_size": [w0, h0]} if (w, h) != (w0, h0) else {}), segments=[], seg_i=0, exports=[])
    log("③ 找镜头切换、按最长 %d 秒分段" % SEG_MAX_S)
    cuts = scene_cuts(root / "preview.mp4", fps)
    d["scene_cuts"] = cuts
    set_starts(d, auto_starts(cuts, d["n"], fps))
    save(root, d)
    log(f"长片段：{d['n']} 帧，{len(cuts)} 处镜头切换，分成 {len(d['segments'])} 段")
    return dict(open_seq=sid)


def seg_of(d, gid):
    for k, s in enumerate(d["segments"]):
        if s["id"] == gid:
            return k, s
    raise KeyError(gid)


def open_segment(projects_dir: Path, sid: str, gid: str, log=print):
    """这一段的项目：没有就从母版精确剪出来建一个。返回 dict(open_segment=项目 id)。"""
    root = root_of(projects_dir, sid)
    d = load(root)
    k, s = seg_of(d, gid)
    if s.get("pid") and (projects_dir / s["pid"] / "project.json").is_file():
        return dict(open_segment=s["pid"])
    a, b, fps = s["start"], s["end"], d["fps"]
    n = b - a + 1
    log(f"剪第 {k + 1} 段：第 {a}–{b} 帧（{n} 帧，{n / fps:.1f} 秒）")
    clip = root / "segments" / f"{d['name']}_第{k + 1}段.mp4"
    clip.parent.mkdir(exist_ok=True)
    fc = f"[0:v]trim=start_frame={a}:end_frame={b + 1},setpts=PTS-STARTPTS[v]"
    maps = ["-map", "[v]"]
    if d.get("audio"):
        fc += f";[0:a]atrim=start={a / fps:.6f}:end={(b + 1) / fps:.6f},asetpts=PTS-STARTPTS[a]"
        maps += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    _run([FFMPEG, "-v", "error", "-y", "-i", root / "master.mp4", "-filter_complex", fc, *maps,
          "-fps_mode", "cfr", "-r", f"{fps}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "12", clip])
    got = vprobe(clip)["n"]
    if got != n:
        raise RuntimeError(f"剪出来 {got} 帧，应该是 {n} 帧")
    log("建这一段的项目（预览帧…）")
    p = Project.create(projects_dir, str(clip))
    if p.data["n"] != n:
        raise RuntimeError(f"这一段的项目有 {p.data['n']} 帧，应该是 {n} 帧")
    p.data["name"] = f"{d['name']} · 第{k + 1}段"
    p.data["seq"] = {"id": sid, "seg": gid}
    p.save()
    try:
        clip.unlink()                                     # 项目里已经拷了一份
    except OSError:
        pass
    d = load(root)                                        # 期间可能被改过：重新读再写
    _, s = seg_of(d, gid)
    s["pid"] = p.data["id"]
    save(root, d)
    return dict(open_segment=p.data["id"])


def seg_results(projects_dir: Path, s):
    """这一段的项目里当前版本的视频结果 [{id, label, time, file}]（没项目 = []）。"""
    if not s.get("pid"):
        return []
    f = projects_dir / s["pid"] / "project.json"
    if not f.is_file():
        return []
    pd = json.loads(f.read_text(encoding="utf-8"))
    cur = pd.get("current")
    return [{"id": r["id"], "label": r.get("label") or "", "time": r.get("time") or "", "file": str(projects_dir / s["pid"] / r["file"])}
            for r in pd.get("results", []) if (r.get("base") or cur) == cur and (projects_dir / s["pid"] / r["file"]).is_file()]


def pick(projects_dir: Path, s):
    """这一段导出时用哪个：(结果文件 或 None = 原片, 说明)。"""
    use = s.get("use") or "auto"
    rs = seg_results(projects_dir, s)
    if use == "orig" or not rs:
        return None, "原视频"
    r = rs[-1] if use == "auto" else next((x for x in rs if x["id"] == use), None)
    return (r["file"], r["label"] or r["id"]) if r else (None, "原视频（选的结果不在了）")


def _concat_filter(d, files, W, H, inputs):
    """每段：files[k] = 结果文件（缩放补边到 W×H、补齐 / 截到这一段帧数）或 None（从 inputs[0] 按帧号剪）。返回 filter 字符串。"""
    fps, chains = d["fps"], []
    for k, s in enumerate(d["segments"]):
        a, b = s["start"], s["end"]
        L = b - a + 1
        f = files[k]
        if f is None:
            # setsar=1：原预览的像素不一定是正方形（缩成 640 宽时会出 399:400），和结果段的 SAR 不同 concat 会直接失败
            chains.append(f"[0:v]trim=start_frame={a}:end_frame={b + 1},setpts=PTS-STARTPTS,setsar=1,format=yuv420p[v{k}]")
        else:
            inputs.append(Path(f))
            i = len(inputs) - 1
            chains.append(f"[{i}:v]fps={fps},scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                          f"tpad=stop_mode=clone:stop={L},trim=end_frame={L},setpts=PTS-STARTPTS,format=yuv420p[v{k}]")
    return ";".join(chains) + ";" + "".join(f"[v{k}]" for k in range(len(chains))) + f"concat=n={len(chains)}:v=1:a=0[v]"


def export(projects_dir: Path, sid: str, log=print):
    root = root_of(projects_dir, sid)
    d = load(root)
    W, H, fps, n = d["w"], d["h"], d["fps"], d["n"]
    inputs, files = [root / "master.mp4"], []
    for k, s in enumerate(d["segments"]):
        f, what = pick(projects_dir, s)
        log(f"第 {k + 1} 段（{s['end'] - s['start'] + 1} 帧）：{what}" + ("（锁定）" if s.get("lock") else ""))
        files.append(f)
    fc = _concat_filter(d, files, W, H, inputs)
    chains = d["segments"]
    out_dir = root / "导出export"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"全片_{len(d.get('exports', [])) + 1}.mp4"
    script = out_dir / "_filter.txt"
    script.write_text(fc, encoding="utf-8")
    cmd = [FFMPEG, "-v", "error", "-y"]
    for x in inputs:
        cmd += ["-i", x]
    cmd += ["-filter_complex_script", script, "-map", "[v]"]
    if d.get("audio"):
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-fps_mode", "cfr", "-r", f"{fps}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "14", "-t", f"{n / fps:.6f}", out]
    log(f"拼接 {len(chains)} 段 → {out.name}")
    _run(cmd)
    script.unlink(missing_ok=True)
    got = vprobe(out)["n"]
    if got != n:
        log(f"注意：导出 {got} 帧，原片 {n} 帧")
    d = load(root)
    d.setdefault("exports", []).append({"file": str(out.relative_to(root)), "time": time.strftime("%m-%d %H:%M"), "n": got})
    save(root, d)
    log(f"导出完成：{out}")
    return {}


# ---------- 锁定：全片里用的结果定死；分段页整条预览里换成它 ----------
def set_lock(projects_dir: Path, d, gid, on):
    """on = True：这一段「全片里用」定死成现在实际用的那条（auto → 最新的结果 id，没有结果 = 原视频）。"""
    _, s = seg_of(d, gid)
    if on:
        if (s.get("use") or "auto") == "auto":
            rs = seg_results(projects_dir, s)
            s["use"] = rs[-1]["id"] if rs else "orig"
        s["lock"] = True
    else:
        s.pop("lock", None)
    return s


def mix_files(projects_dir: Path, d):
    """整条预览里每段用什么：锁定的段 = 它的结果文件，其余 None（原视频）。"""
    return [pick(projects_dir, s)[0] if s.get("lock") else None for s in d["segments"]]


def mix_key(projects_dir: Path, d):
    """预览要不要重拼：锁定的段（起止 + 文件 + 文件时间）变了才重拼；没有锁定的 = ""（用原预览）。"""
    parts = []
    for s, f in zip(d["segments"], mix_files(projects_dir, d)):
        if f:
            p = Path(f)
            parts.append(f"{s['start']}-{s['end']}:{p.name}:{int(p.stat().st_mtime) if p.exists() else 0}")
    return "|".join(parts)


def build_mix(projects_dir: Path, sid: str, log=print):
    """分段页的整条预览：锁定的段换成锁定的结果（缩到预览大小），其余从原预览剪；声音用原预览的。"""
    root = root_of(projects_dir, sid)
    d = load(root)
    if preview_old(root, d):                             # 设置里换了分段预览清晰度（或旧长片段还是 640 宽）：先重做预览
        d["preview_long"] = make_preview(root, d["w"], d["h"], log)
        d["mix_built"] = None
        save(root, d)
    key = mix_key(projects_dir, d)
    for f in root.glob("strip_mix_*.jpg"):
        f.unlink(missing_ok=True)
    if not key:
        (root / "preview_mix.mp4").unlink(missing_ok=True)
        log("没有锁定的段：整条预览用原视频")
    else:
        pv = vprobe(root / "preview.mp4")
        inputs = [root / "preview.mp4"]
        files = mix_files(projects_dir, d)
        fc = _concat_filter(d, files, pv["w"], pv["h"], inputs)
        script = root / "_mix_filter.txt"
        script.write_text(fc, encoding="utf-8")
        tmp = root / "preview_mix.tmp.mp4"
        cmd = [FFMPEG, "-v", "error", "-y"]
        for x in inputs:
            cmd += ["-i", x]
        cmd += ["-filter_complex_script", script, "-map", "[v]"]
        if pv.get("audio"):
            cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "96k"]
        cmd += ["-fps_mode", "cfr", "-r", f"{d['fps']}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "28", "-g", "12",
                "-t", f"{d['n'] / d['fps']:.6f}", "-f", "mp4", tmp]
        log(f"整条预览：{sum(1 for f in files if f)} 段换成锁定的结果")
        _run(cmd)
        script.unlink(missing_ok=True)
        tmp.replace(root / "preview_mix.mp4")
    d = load(root)
    d["mix_built"] = key
    save(root, d)
    return {}


def preview_file(projects_dir: Path, root: Path, d):
    """分段页现在该放哪个预览：有锁定的段、拼好的预览也是最新的 → preview_mix.mp4，否则原预览。"""
    key = mix_key(projects_dir, d)
    mix = root / "preview_mix.mp4"
    return mix if key and d.get("mix_built") == key and mix.exists() else root / "preview.mp4"
