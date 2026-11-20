"""提示词里的 @引用：用户在描述 / 提示词框里输入 @ 选一张参考图，框里存的是「@名称」（比如 @背景图）。
前端发请求时带上 mentions = [{name, key}]（key = 这张图在参考图清单里的身份：base / bg / ref_<id> / first / card:<图层id> /
other:<id> / video）。后端在真正组装好要发给模型的参考图之后，按实际顺序把「@名称」换成这个模型认得的写法：
Qwen = Picture N，MiniMax H3 / H3 Image = <Picture N>（粗剪 = <Video 1>），Seedance 等云端视频 = @图片N（粗剪 = @视频1）。
存进项目的文字不变（还是 @名称），换顺序、删图都不会把编号写死在文字里。"""

PICTURE_QWEN = "Picture {n}"
PICTURE_H3 = "<Picture {n}>"
VIDEO_H3 = "<Video {n}>"
PICTURE_API_VIDEO = "@图片{n}"
VIDEO_API_VIDEO = "@视频{n}"


def parse(raw):
    """请求里的 mentions → [(名称, key)]（来自浏览器：只留合法的几项，名称 / key 长度有上限）。"""
    out = []
    for m in raw if isinstance(raw, list) else []:
        if not isinstance(m, dict):
            continue
        name, key = str(m.get("name") or "").strip(), str(m.get("key") or "").strip()
        if name and key and len(name) <= 60 and len(key) <= 80 and (name, key) not in out:
            out.append((name, key))
    return out[:60]


def resolve(text, mentions, keys, picture=PICTURE_QWEN, video=VIDEO_H3, log=None):
    """把 text 里的「@名称」换成模型写法。keys = 这次实际发给模型的参考图按顺序的 key 列表（图号 = 位置 + 1）；
    key == "video" = 粗剪视频（Video 1）。引用的图这次没传（被跳过 / 没勾）：换成普通名称并在日志里提醒，不会指错图。
    名称长的先换（「背景图」不会被「背景」截掉）。"""
    if not text or not mentions:
        return text
    pos = {k: i + 1 for i, k in enumerate(keys)}
    for name, key in sorted(mentions, key=lambda m: -len(m[0])):
        tok = "@" + name
        if tok not in text:
            continue
        if key == "video":
            rep = video.format(n=1)
        elif key in pos:
            rep = picture.format(n=pos[key])
        else:
            rep = name
            if log:
                log(f"  注意：提示词里引用的「@{name}」这次没有传给模型，按普通文字处理")
        text = text.replace(tok, rep)
    return text


def picture_format(engine):
    """图片生成走 ComfyUI 时用哪种写法：MiniMax H3 Image = <Picture N>；Qwen、自己的工作流、改图走云端 API = Picture N。"""
    cfg = getattr(engine, "cfg", None) or {}
    if isinstance(cfg, dict) and cfg.get("edit") == "api":
        return PICTURE_QWEN
    base = getattr(engine, "base", engine)
    if getattr(base, "image_workflow", None):
        return PICTURE_QWEN
    m = (getattr(base, "QWEN_MODELS", None) or {}).get(getattr(base, "qwen_model", ""), {})
    return PICTURE_H3 if m.get("family") == "h3" else PICTURE_QWEN
