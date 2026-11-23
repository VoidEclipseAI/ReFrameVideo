"""参考图标签的存储：首帧、尾帧、背景图、资料卡。都是“这个视频的图”，先在图上用图片模型出几张候选、选用一张，
生成视频时自动用上（首帧 / 尾帧 / 资料卡 / 背景图都当参考图）。

这里只管数据：标签的增删查、候选列表、选用、上传、额外参考图、过期签名，以及资料卡抠图的纯函数。
取画面和补图范围在 refedit.py，提示词在 refprompt.py，出候选在 refgen.py。

project.data["images"] = [{id, kind, label, oid?, frame?, sig, prompt, cands: [名字], pick: 名字 | None, refs: [额外参考图]}]
文件在 项目/images/<id>/：base.png（取来的原图，空白处透明）、input.png（交给图片模型的图）、
cand_<seed>.png（候选）、ref_<rid>.png（额外参考图的副本）。背景图标签不存文件，直接用全景图（panorama/）。
"""
import io
import json
import uuid

import cv2
import numpy as np
from PIL import Image

from project import obj_word
from regions import MASK_OPTS, MASK_OPTS_V1, frame_rect

KINDS = {"first": "首帧", "last": "尾帧", "bg": "背景图", "card": "资料卡", "key": "关键帧"}
STILLS = ("first", "last", "key")   # 视频里某一帧的画面（按图层摆放取）：首帧 = 第 0 帧、尾帧 = 最后一帧、关键帧 = 建的时候播放头停的那一帧
N_CANDS = 4
CARD_CANVAS = (1536, 1024)          # 三视图要横向的画布（和 lora_unified/make_refs.py 一样）



def open_upload(data):
    """用户上传的图片 → RGB：按 EXIF 方向转正（手机竖拍的照片不会横着进来）。"""
    from PIL import ImageOps
    return ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")

def items(p):
    return p.data.setdefault("images", [])


def get(p, iid):
    for it in items(p):
        if it["id"] == iid:
            return it
    raise KeyError(iid)


def folder(p, iid):
    d = p.image_dir(get(p, iid))
    d.mkdir(parents=True, exist_ok=True)
    return d


HOLE_OPTS = MASK_OPTS   # 空区处理的选项：改了首帧 / 尾帧要重新取（没开的不进签名，加新选项不让旧选用过期）


def layer_sig(p, vid=None):
    """某个版本（默认当前）的图层状态（变换、显示、层级）。首帧 / 尾帧取完之后图层又被改过，就提示重新取。"""
    vid = vid or p.data["current"]
    objs = sorted((o for o in p.objects_in(vid) if not o.get("prefill")), key=lambda o: o["id"])   # 背景预填只影响视频生成，不让首帧 / 尾帧过期
    ext = lambda o: (o["ext_masks"] if isinstance(o.get("ext_masks"), list)                # 跟随蒙版 / 扩大重绘范围改了：首帧 / 尾帧也要重新取
                     else {k: v for k, v in (o.get("box_ext") or {}).items() if v})    # （只有旧 box_ext 的项目签名和以前一样）
    cr = p.crop_rows() if hasattr(p, "crop_rows") else None                       # 裁切黑边也算画幅：改了首帧 / 尾帧要重新取（没裁切黑边时签名和以前一样）
    return json.dumps([vid, frame_rect(p) + (list(cr) if cr else [])] + [[o["id"], o.get("tf"), o.get("visible", True), o.get("z")] + ([ext(o)] if ext(o) else []) + (["off"] if o.get("off") else [])
                                              + ([{k: o[k] for k in HOLE_OPTS if o.get(k)}] if any(o.get(k) for k in HOLE_OPTS) else [])
                                              for o in objs],
                      sort_keys=True)


def PROJ_CLEAN(o):
    from project import clean_opts
    return clean_opts(o)


def edit_sig(p, vid=None):
    """视频里图层的全部编辑（摆放 + 蒙版 + 空区处理 + 重绘区外扩 + 手动修改）：任何一项改了，某一帧的画面 / 空区就会变。"""
    objs = [o for o in p.objects_in(vid or p.data["current"]) if not o.get("prefill")]
    return layer_sig(p, vid) + json.dumps([[o["id"], o.get("edge"), o.get("grow"), o.get("box_ext"),
                                       [None] + [o.get(k) for k in MASK_OPTS_V1] + [{k: o[k] for k in MASK_OPTS[len(MASK_OPTS_V1):] if o.get(k)}][:any(o.get(k) for k in MASK_OPTS[len(MASK_OPTS_V1):])], o.get("edits"), sum((o.get("medits") or {}).values())] + ([o["parts"]] if o.get("parts") else [])
                                      + ([o["ext_masks"]] if o.get("ext_masks") else [])
                                      + ([o["keep_masks"]] if o.get("keep_masks") else [])   # 主体局部保留（没有时签名不变）
                                      + ([PROJ_CLEAN(o)] if any(PROJ_CLEAN(o)) else [])         # 蒙版清理（没开时签名和以前一样）
                                      for o in objs])


def pick_sig(it):
    """选用的那张候选是按哪一版图层出的（cand_sig；旧标签没记就用取画面时的 sig）。"""
    return (it.get("cand_sig") or {}).get(it.get("pick"), it.get("sig"))


SIG_TOL_PX = 8          # 首帧 / 尾帧取完之后图层只挪了这么几个像素（缩放 1%、旋转 0.5° 以内）：不算过期，照样用


def sig_close(a, b):
    """两份图层签名是否“差不多”：版本、画框、显示、层级一样，位置差 ≤ SIG_TOL_PX，缩放、旋转几乎没变。"""
    if not a or not b:
        return False
    if a == b:
        return True
    a, b = json.loads(a), json.loads(b)
    if a[:2] != b[:2] or len(a) != len(b):
        return False
    for x, y in zip(a[2:], b[2:]):
        if x[0] != y[0] or x[2:] != y[2:]:
            return False
        tx, ty = x[1] or {}, y[1] or {}
        g = lambda t, k, d: float(t.get(k, d) if t.get(k) is not None else d)
        if (abs(g(tx, "x", 0) - g(ty, "x", 0)) > SIG_TOL_PX or abs(g(tx, "y", 0) - g(ty, "y", 0)) > SIG_TOL_PX
                or abs(g(tx, "sx", 1) - g(ty, "sx", 1)) > 0.01 or abs(g(tx, "sy", 1) - g(ty, "sy", 1)) > 0.01
                or abs(g(tx, "rot", 0) - g(ty, "rot", 0)) > 0.5):
            return False
    return True


def anchors(p, vid=None):
    """合成会不会用上首帧 / 尾帧：ok = 用上；none = 没建这个标签；nopick = 没打勾；stale = 取完后图层改动太大。"""
    cur = layer_sig(p, vid)
    out = {}
    for kind in ("first", "last"):
        it = find(p, kind, vid=vid)
        out[kind] = ("none" if not it else "nopick" if not picked_path(p, kind, vid=vid)
                     else "ok" if sig_close(pick_sig(it), cur) else "stale")
    return out


def still_index(p, it):
    """首帧 / 尾帧 / 关键帧取的是视频第几帧。"""
    if it["kind"] == "key":
        return min(max(int(it.get("frame") or 0), 0), p.data["n"] - 1)
    return 0 if it["kind"] == "first" else p.data["n"] - 1


def keys(p, vid=None):
    """这个版本的关键帧标签（可以多张，按帧号排）。"""
    vid = vid or p.data["current"]
    return sorted((it for it in items(p) if it["kind"] == "key" and it.get("version") == vid), key=lambda it: (still_index(p, it), it["id"]))


def item_pick_path(p, it):
    """这个标签选用的那张（没有就 None）。"""
    if not it or not it.get("pick"):
        return None
    f = p.image_dir(it) / f"{it['pick']}.png"
    return f if f.exists() else None


def find(p, kind, oid=None, vid=None):
    """图片标签跟着版本走：首帧 / 尾帧取画面时记下 version，只在那个版本里用；没记 version 的旧标签所有版本都能用
    （同一版本里有新的就用新的）。资料卡按图层找（图层本来就属于某个版本）；背景图全项目一个。"""
    if kind == "card":
        return next((it for it in items(p) if it["kind"] == kind and it.get("oid") == oid), None)
    vid = vid or p.data["current"]
    mine = [it for it in items(p) if it["kind"] == kind and it.get("version") in (vid, None)]
    return next((it for it in mine if it.get("version") == vid), None) or next(iter(mine), None)


def add_cand_upload(p, iid, data):
    """上传一张图直接当候选（比如商品图、自己 PS 好的首帧），之后和 Qwen 出的候选一样选用。尺寸不动。"""
    import io
    it = get(p, iid)
    seed = next_cand(p, it)
    name = f"cand_{seed}"
    open_upload(data).save(folder(p, iid) / f"{name}.png")
    it["cands"].append(name)
    if it["kind"] in STILLS:
        it.setdefault("cand_sig", {})[name] = layer_sig(p)   # 上传的当成按现在的图层做的
    import time
    it.setdefault("cand_info", {})[name] = {"time": time.strftime("%Y-%m-%d %H:%M"), "upload": True}
    link_cand(p, it, name)
    p.save()
    return it


def card_source(frames, m):
    """物体够大（≥ 最大面积的 60%）的帧里挑最清楚的一帧，抠到中灰底上，放进横向画布中间。"""
    area = m.reshape(len(m), -1).sum(1)
    cand = [i for i in range(len(m)) if area[i] >= 0.6 * area.max()][::max(1, len(m) // 40)]

    def sharp(i):
        g = cv2.cvtColor(frames[i], cv2.COLOR_RGB2GRAY).astype(np.float32)
        inner = cv2.erode(m[i].astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
        return float(cv2.Laplacian(g, cv2.CV_32F)[inner].var()) if inner.any() else 0.0

    k = max(cand, key=sharp)
    return card_crop(frames[k], m[k]), k


def card_crop(frame, m):
    """把一帧里的物体抠到中灰底上，放进横向画布（CARD_CANVAS）中间。"""
    keep = cv2.dilate(m.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    ys, xs = np.where(keep)
    crop = np.where(keep[..., None], frame, 128).astype(np.uint8)[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    W, H = CARD_CANVAS
    s = min(0.85 * H / crop.shape[0], 0.6 * W / crop.shape[1])
    crop = cv2.resize(crop, (max(1, round(crop.shape[1] * s)), max(1, round(crop.shape[0] * s))),
                      interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
    canvas = np.full((H, W, 3), 128, np.uint8)
    y, x = (H - crop.shape[0]) // 2, (W - crop.shape[1]) // 2
    canvas[y:y + crop.shape[0], x:x + crop.shape[1]] = crop
    return canvas


# ---------- 额外参考图：Qwen 出候选时一起发过去（Picture 3、4…；首帧 / 尾帧的 Picture 2 是背景参考） ----------
# 来源：上传的图片、别的图片标签里的图（候选 / 原图）、资料卡还可以从视频某一帧把这个物体抠出来。
# 每张存一份副本 ref_<rid>.png（别的标签以后改了也不影响这里），note = 写进提示词的那句说明（英文，可改）。
REF_NOTE_FRAME = "Picture {n} shows the same {word} in another frame of the video: use it to get the face, clothing and details right."
# 资料卡当「衣着参考」，不是往画面里贴的一张图：写「照这张穿衣服」时 Qwen 会把卡当画面内容（2026-11-13 实测：首帧人物变形、手套不见；改成只取衣着细节后正常）。只在有资料卡参考图时用。
REF_NOTE_CARD = ("Picture {n} is a clothing reference for the {word}, not part of the scene: it shows the clothes, "
                 "colours, materials and accessories the {word} wears. Take only those details from it; do not draw "
                 "Picture {n} itself into the frame, and do not copy its layout, pose or background.")
REF_NOTE_OTHER = "Picture {n} is a reference image: use it for the appearance and clothing."
# 老版本写的资料卡说明句（项目里已经存下了）：出候选时替换成上面这句，不用用户手动删了重加
OLD_REF_NOTE_CARD = "Picture {n} is the character reference sheet of the "


def ref_list(it):
    return it.setdefault("refs", [])


def _add_ref(p, it, img, src, note, **extra):
    rid = uuid.uuid4().hex[:8]
    Image.fromarray(img).save(folder(p, it["id"]) / f"ref_{rid}.png", compress_level=1)
    ref_list(it).append(dict(id=rid, src=src, note=note, **extra))
    p.save()
    return it


def add_ref_upload(p, iid, data, replace=""):
    """上传的图片（请求体里的图片文件）。"""
    import io
    it = get(p, iid)
    img = np.array(open_upload(data))
    if replace:
        refs = ref_list(it)
        for index, old in enumerate(refs):
            if old["id"] == replace:
                rid = uuid.uuid4().hex[:8]
                Image.fromarray(img).save(folder(p, iid) / f"ref_{rid}.png")
                refs[index] = dict(id=rid, src="upload", note=old.get("note", REF_NOTE_OTHER))
                p.save()
                return it
        raise ValueError("要替换的参考图已移除")
    return _add_ref(p, it, img, "upload", REF_NOTE_OTHER)


def add_ref_tab(p, iid, from_iid, name):
    """别的图片标签里的一张图（name = 候选名 / base）。从资料卡拿的，说明默认写成“照这张穿衣服”。"""
    it, src = get(p, iid), get(p, from_iid)
    f = file_path(p, from_iid, name)
    img = np.array(Image.open(f).convert("RGB"))
    if src["kind"] == "card":
        note = REF_NOTE_CARD.replace("{word}", obj_word(p.obj(src["oid"])))
    else:
        note = REF_NOTE_OTHER
    return _add_ref(p, it, img, "tab", note, from_iid=from_iid, name=name, label=src["label"])


def add_ref_frame(p, iid, i):
    """资料卡：从这个物体的版本里第 i 帧把它抠出来（灰底，和资料卡原图一样的画布）。"""
    it = get(p, iid)
    if it["kind"] != "card":
        raise ValueError("只有资料卡能从视频帧抠物体")
    o = p.obj(it["oid"])
    m = p.masks(o["id"])
    if not 0 <= i < len(m) or not m[i].any():
        raise ValueError(f"第 {i} 帧里没有这个物体")
    img = card_crop(p.frame(o["version"], i), m[i])
    return _add_ref(p, it, img, "frame", REF_NOTE_FRAME.replace("{word}", obj_word(o)), frame=int(i))


def set_ref_note(p, iid, rid, note):
    it = get(p, iid)
    r = next(r for r in ref_list(it) if r["id"] == rid)
    r["note"] = str(note).strip()
    p.save()
    return it


def remove_ref(p, iid, rid):
    it = get(p, iid)
    it["refs"] = [r for r in ref_list(it) if r["id"] != rid]
    p.save()
    return it


def next_cand(p, it):
    """下一张候选的编号：比列表里和文件夹里已有的都大（重新取画面后列表清空，但旧文件还在，不能覆盖，
    也不能和旧文件同名，否则浏览器会显示缓存里的旧图）。"""
    import re
    nums = [int(m.group(1)) for f in folder(p, it["id"]).glob("cand_*.png") for m in [re.fullmatch(r"cand_(\d+)\.png", f.name)] if m]
    return max(nums + [int(c.split("_")[1]) for c in it["cands"]] + [0]) + 1


def pick(p, iid, name):
    """挑一张（name = None 取消）。挑了资料卡，修复标签里的“有资料卡”自动勾上。"""
    it = get(p, iid)
    if name is not None and name not in it["cands"]:
        raise ValueError(f"没有这张候选：{name}")
    it["pick"] = name
    _link_pick(p, it)
    st = p.data.setdefault("edit_prompt", dict(tags=[], scene="", has_bg=False, has_ref=False))
    st["has_ref"] = any(x["kind"] == "card" and x.get("pick") for x in items(p))
    st["has_bg"] = bool((p.data.get("panorama") or {}).get("filled")) and find(p, "bg") is not None
    p.save()
    return it


def _kind_name(it):
    import re
    if it["kind"] == "key":                              # 关键帧可以多张：文件名带帧号
        return f"关键帧{int(it.get('frame') or 0)}"
    return KINDS[it["kind"]] if it["kind"] != "card" else re.sub(r'[\\/:*?"<>|]', "_", it.get("label", "资料卡")).replace(" · ", "_")


def _hardlink(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.unlink(missing_ok=True)
    try:
        dst.hardlink_to(src)
    except OSError:
        import shutil
        shutil.copy2(src, dst)


def cand_file_name(p, it, name):
    """候选图对外的文件名：项目名_vN_首帧_K.png（K = cand_K 的编号）；背景图全项目共用，没有版本：项目名_背景图_K.png。
    结果文件夹里的硬链接（link_cand）和「导出」直接下载用同一个名字。"""
    import re
    m = re.fullmatch(r"cand_(\d+)", name)
    k = m.group(1) if m else name
    parts = (it["version"], _kind_name(it), k) if it.get("version") and it["kind"] != "bg" else (_kind_name(it), k)
    return f"{p.out_name(*parts)}.png"


def link_cand(p, it, name):
    """新出的候选在 结果result/<vN>/图片images/项目名_vN_首帧_K.png 放一份（硬链接，不占空间；K = cand_K 的编号）。
    中间文件里的 cand_K.png 照旧（界面 / 接口都按它找）。旧结构、背景图（全项目共用）不做。"""
    if not p.v3 or not it.get("version") or it["kind"] == "bg":
        return
    src = p.image_dir(it) / f"{name}.png"
    if src.exists():
        _hardlink(src, p.version_results_dir(it["version"], "图片images") / cand_file_name(p, it, name))


def _link_pick(p, it):
    """选用的那张：结果result/<vN>/图片images/项目名_vN_首帧_选用.png（硬链接）；取消选用就删掉。旧结构不做。
    以前放在 结果result/图片images/首帧_vN.png 的那份也一起去掉（只是链接，原图在中间文件里）。"""
    if not p.v3 or not it.get("version") or it["kind"] == "bg":
        return
    name, v = _kind_name(it), it["version"]
    (p.results_dir("图片images") / f"{name}_{v}.png").unlink(missing_ok=True)
    dst = p.version_results_dir(v, "图片images") / f"{p.out_name(v, name, '选用')}.png"
    dst.unlink(missing_ok=True)
    src = p.image_dir(it) / f"{it['pick']}.png" if it.get("pick") else None
    if src and src.exists():
        _hardlink(src, dst)


def remove(p, iid):
    p.data["images"] = [it for it in items(p) if it["id"] != iid]
    p.save()


def picked_path(p, kind, oid=None, vid=None):
    """合成时用：这类图片挑好的那张（没有就 None）。"""
    return item_pick_path(p, find(p, kind, oid, vid))


def file_path(p, iid, name):
    f = p.image_dir(get(p, iid)) / f"{name}.png"
    if not f.exists() or not name.replace("_", "").isalnum():
        raise FileNotFoundError(name)
    return f


# ---------- 其他图片：只在视频生成栏里当参考图，不建图片标签（首帧 / 尾帧 / 背景图 / 资料卡还是走图片标签） ----------
# project.data["gen_refs"] = [{id, label}]，文件在 项目/gen_refs/gen_<id>.png
def gen_refs(p):
    return p.data.setdefault("gen_refs", [])


def gen_ref_folder(p):
    d = p.gen_ref_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def gen_ref_path(p, rid):
    return gen_ref_folder(p) / f"gen_{rid}.png"


def add_gen_ref(p, data):
    """上传一张「其他图片」：存一份，按顺序起名「其他图片 N」。"""
    import io
    rid = uuid.uuid4().hex[:8]
    open_upload(data).save(gen_ref_path(p, rid), compress_level=1)
    n = int(p.data.get("gen_ref_n") or len(gen_refs(p))) + 1
    p.data["gen_ref_n"] = n
    it = dict(id=rid, label=f"其他图片 {n}")
    gen_refs(p).append(it)
    p.save()
    return it


def remove_gen_ref(p, rid):
    """从列表里去掉（文件留着，不真删）。"""
    p.data["gen_refs"] = [r for r in gen_refs(p) if r["id"] != rid]
    p.save()
    return p.data["gen_refs"]
