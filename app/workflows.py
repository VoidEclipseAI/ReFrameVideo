"""用户自己的 ComfyUI 工作流（ComfyUI「导出 (API)」的格式），用 ComfyUI-Reframe 节点包的标记节点标出入口和出口（节点本身只是直通）。

改图工作流（workflows/image/*.json）：
  ReframeImageInput(index)  第 1 张 = 要改的图，2、3… = 参考图（顺序 = 提示词里的 Picture N）
  ReframeMaskInput          遮罩（白 = 重画）；可以没有（软件按遮罩贴回）
  ReframeTextInput          提示词
  ReframeIntInput(name)     seed / width / height（第 1 张图的宽高）
  ReframeOutput             结果（正好一个）
fill() 把软件上传的图、提示词、数字接到这些节点上，再在 ReframeOutput 的来源后面接一个 SaveImage 取结果。

视频工作流（workflows/video/*.json，2026-11-18）：同一套节点，另加
  ReframeVideoInput(name)   rough_cut = 粗剪（空区灰 128，软件算好的宽高、原片帧数）；guide = <Video 1>（粗剪缩到 768 宽）
  ReframeMaskInput          每帧一张重绘 mask（白 = 重绘）；可以没有
  ReframeImageInput(index)  1、2… = 视频生成栏勾的参考图（Picture 1、2…）；可以一张都没有
  ReframeTextInput(name)    prompt = 软件写的完整提示词（H3 六段 + 空区分析）；user = 用户自己写的原文
  ReframeIntInput(name)     seed / width / height / frames
  ReframeOutput             成片的逐帧图片（正好一个）
fill_video() 把视频接成 LoadVideo → GetVideoComponents，成片接 CreateVideo → SaveVideo。
"""
import copy
import json
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "workflows" / "image"
VDIR = DIR.parent / "video"
PREFIX = "wf-"                       # 模型下拉里工作流的 key 前缀
IMG, MASK, TEXT, INT, OUT = "ReframeImageInput", "ReframeMaskInput", "ReframeTextInput", "ReframeIntInput", "ReframeOutput"
VID = "ReframeVideoInput"
INTS = ("seed", "width", "height")
VINTS = ("seed", "width", "height", "frames")
VIDEOS = ("rough_cut", "guide")


def names(folder=None):
    folder = folder or DIR
    return sorted(f.stem for f in folder.glob("*.json")) if folder.is_dir() else []


def video_names():
    return names(VDIR)


def _read(folder, name):
    f = folder / f"{name}.json"
    if not f.is_file():
        raise ValueError(f"没有这个工作流：{name}")
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"工作流 {name} 读不出来：{e}")


def load(name):
    wf = _read(DIR, name)
    needs(wf)
    return wf


def load_video(name):
    wf = _read(VDIR, name)
    needs_video(wf)
    return wf


def _of(wf, cls):
    return [(k, n) for k, n in wf.items() if n.get("class_type") == cls]


def _api_format(wf):
    if not isinstance(wf, dict) or "nodes" in wf or not all(isinstance(n, dict) and "class_type" in n for n in wf.values()):
        raise ValueError("不是 API 格式：在 ComfyUI 里用「导出 (API)」再存一次")


def _indexes(wf, first=False):
    """Reframe Image Input 的 index 列表（检查：直接填的数字、不重复、从 1 连续排）。first：必须有 index 1（改图要的原图）。"""
    idx = []
    for k, n in _of(wf, IMG):
        i = n.get("inputs", {}).get("index")
        if not isinstance(i, int) or i < 1:
            raise ValueError(f"Reframe Image Input（节点 {k}）的 index 要直接填数字")
        idx.append(i)
    if first and 1 not in idx:
        raise ValueError("缺 Reframe Image Input（index = 1，要改的图）")
    if len(set(idx)) != len(idx):
        raise ValueError("有两个 Reframe Image Input 的 index 一样")
    if sorted(idx) != list(range(1, len(idx) + 1)):
        raise ValueError(f"Reframe Image Input 的 index 要从 1 连续排（现在是 {sorted(idx)}）")
    return idx


def _one_output(wf):
    outs = _of(wf, OUT)
    if len(outs) != 1:
        raise ValueError(f"Reframe Output 要正好一个（现在 {len(outs)} 个）")
    if not isinstance(outs[0][1].get("inputs", {}).get("images"), list):
        raise ValueError("Reframe Output 的 images 没有连线")


def _check_ints(wf, allowed):
    for k, n in _of(wf, INT):
        if n.get("inputs", {}).get("name") not in allowed:
            raise ValueError(f"Reframe Number Input（节点 {k}）的 name 只能是 {' / '.join(allowed)}")


def _has_seed(wf):
    return any(n.get("inputs", {}).get("name") == "seed" for _, n in _of(wf, INT))


def _prune(g, root):
    """只留 root 用得到的节点：用户自己接的 Save / Preview、别的输出分支都不跑。"""
    keep, todo = set(), [root]
    while todo:
        k = todo.pop()
        if k in keep or k not in g:
            continue
        keep.add(k)
        todo += [v[0] for v in g[k]["inputs"].values() if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)]
    return {k: n for k, n in g.items() if k in keep}


def needs(wf):
    """检查并返回这个改图工作流要什么：{images: 要几张图, mask: 要不要遮罩, seed: 有没有 seed 节点}。不合格抛 ValueError（中文原因）。"""
    _api_format(wf)
    idx = _indexes(wf, first=True)
    _one_output(wf)
    _check_ints(wf, INTS)
    return dict(images=len(idx), mask=bool(_of(wf, MASK)), seed=_has_seed(wf))


def fill(wf, images, mask, prompt, seed, size):
    """images：上传到 ComfyUI 后的文件名（第 1 张、第 2 张…，至少 needs()["images"] 张）；mask：遮罩文件名（工作流有遮罩节点时必须给）；
    size = (宽, 高)。返回 (填好的工作流, 取结果的节点 id)。不改传进来的 wf。"""
    need = needs(wf)
    if len(images) < need["images"]:
        raise ValueError(f"这个工作流要 {need['images']} 张图（原图 + 参考图），这次只有 {len(images)} 张")
    g = copy.deepcopy(wf)
    for k, n in _of(g, IMG):
        i = n["inputs"]["index"]
        g[f"rf_img{i}"] = {"class_type": "LoadImage", "inputs": {"image": images[i - 1]}}
        n["inputs"]["image"] = [f"rf_img{i}", 0]
    if need["mask"]:
        if not mask:
            raise ValueError("这个工作流有遮罩节点，但没有给遮罩")
        g["rf_mask_img"] = {"class_type": "LoadImage", "inputs": {"image": mask}}
        g["rf_mask"] = {"class_type": "ImageToMask", "inputs": {"image": ["rf_mask_img", 0], "channel": "red"}}
        for k, n in _of(g, MASK):
            n["inputs"]["mask"] = ["rf_mask", 0]
    for k, n in _of(g, TEXT):
        n["inputs"]["text"] = prompt
    val = dict(seed=int(seed), width=int(size[0]), height=int(size[1]))
    for k, n in _of(g, INT):
        n["inputs"]["value"] = val[n["inputs"]["name"]]
    src = _of(g, OUT)[0][1]["inputs"]["images"]
    g["rf_save"] = {"class_type": "SaveImage", "inputs": {"images": list(src), "filename_prefix": "videops/workflow"}}
    return _prune(g, "rf_save"), "rf_save"


def needs_video(wf):
    """视频工作流要什么：{images: 几张参考图, videos: 用到的视频, mask, texts: 用到的提示词, seed}。不合格抛 ValueError。"""
    _api_format(wf)
    idx = _indexes(wf)
    vids = []
    for k, n in _of(wf, VID):
        v = n.get("inputs", {}).get("name")
        if v not in VIDEOS:
            raise ValueError(f"Reframe Video Input（节点 {k}）的 name 只能是 {' / '.join(VIDEOS)}")
        vids.append(v)
    if "rough_cut" not in vids:
        raise ValueError("缺 Reframe Video Input（name = rough_cut，粗剪）")
    _one_output(wf)
    _check_ints(wf, VINTS)
    texts = {n.get("inputs", {}).get("name") or "prompt" for _, n in _of(wf, TEXT)}
    return dict(images=len(idx), videos=sorted(set(vids)), mask=bool(_of(wf, MASK)), texts=sorted(texts), seed=_has_seed(wf))


def fill_video(wf, videos, mask, images, prompts, ints, fps):
    """videos：{"rough_cut": 上传名, "guide": 上传名}；mask：重绘 mask 视频的上传名（png 编码，白 = 重绘）；
    images：参考图上传名（第 1、2…，至少 needs_video()["images"] 张）；prompts：{"prompt": 软件写的, "user": 用户原文}；
    ints：{seed, width, height, frames}；fps：成片帧率。返回 (填好的工作流, 取结果的节点 id)。不改传进来的 wf。"""
    need = needs_video(wf)
    if len(images) < need["images"]:
        raise ValueError(f"这个工作流要 {need['images']} 张参考图，这次只勾了 {len(images)} 张")
    g = copy.deepcopy(wf)
    for name in need["videos"]:
        if not videos.get(name):
            raise ValueError(f"这次没有 {name} 视频")
        g[f"rf_{name}_load"] = {"class_type": "LoadVideo", "inputs": {"file": videos[name]}}
        g[f"rf_{name}"] = {"class_type": "GetVideoComponents", "inputs": {"video": [f"rf_{name}_load", 0]}}
    for k, n in _of(g, VID):
        n["inputs"]["images"] = [f"rf_{n['inputs']['name']}", 0]
    if need["mask"]:
        if not mask:
            raise ValueError("这个工作流有遮罩节点，但没有给遮罩")
        g["rf_mask_load"] = {"class_type": "LoadVideo", "inputs": {"file": mask}}
        g["rf_mask_frames"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["rf_mask_load", 0]}}
        g["rf_mask"] = {"class_type": "ImageToMask", "inputs": {"image": ["rf_mask_frames", 0], "channel": "red"}}
        for k, n in _of(g, MASK):
            n["inputs"]["mask"] = ["rf_mask", 0]
    for k, n in _of(g, IMG):
        i = n["inputs"]["index"]
        g[f"rf_img{i}"] = {"class_type": "LoadImage", "inputs": {"image": images[i - 1]}}
        n["inputs"]["image"] = [f"rf_img{i}", 0]
    for k, n in _of(g, TEXT):
        n["inputs"]["text"] = prompts.get(n["inputs"].get("name") or "prompt", "")
    for k, n in _of(g, INT):
        n["inputs"]["value"] = int(ints[n["inputs"]["name"]])
    src = _of(g, OUT)[0][1]["inputs"]["images"]
    # CreateVideo / SaveVideo 的参数和 engine.build_h3_graph 一样（ComfyUI 0.45 要这些）
    g["rf_video"] = {"class_type": "CreateVideo", "inputs": {"images": list(src), "fps": float(fps), "bit_depth": "auto",
                                                             "color_space": "sRGB", "codec": "none"}}
    g["rf_save"] = {"class_type": "SaveVideo", "inputs": {"video": ["rf_video", 0], "filename_prefix": "videops/workflow",
                                                          "format": "auto", "codec": "auto", "format.codec": "auto"}}
    return _prune(g, "rf_save"), "rf_save"
