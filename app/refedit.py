"""参考图编辑：一张参考图（首帧 / 尾帧 / 资料卡）的画面怎么来、图片模型要补哪里。
首帧 / 尾帧 = 视频编辑停在那一帧：图层摆放跟视频；蒙版、空区处理、重绘区外扩可以单图覆盖（只存进这张图，不影响视频）。
补图范围 = 这一帧的空区 → 整张图圆滑 / 外扩 → 贴人一圈 / 整体外扩 / 包含遮挡物。
"""
import io
import json
import uuid

import cv2
import numpy as np
from PIL import Image

from images import KINDS, STILLS, still_index, card_source, open_upload, edit_sig, find, folder, get, items, layer_sig
from project import obj_word
from regions import EMPTY, MASK_OPTS, compose_still, frame_one, frame_rect, to_frame
import panorama as PANO
from refprompt import OBJECT_PROMPT, PERSON_PROMPT, PERSON_WORDS, gen_mode, sys_default, sys_for


def create(p, kind, oid=None, log=print, frame=None):
    """新建一个图片标签（同一种只能有一个，资料卡每个物体一个；关键帧可以多张，frame = 视频第几帧）。返回标签。"""
    if kind not in KINDS:
        raise ValueError(f"未知的图片类型 {kind}")
    old = None if kind == "key" else find(p, kind, oid)
    if old:
        return old
    it = dict(id=("bg" if kind == "bg" else uuid.uuid4().hex[:8]), kind=kind, label=KINDS[kind], cands=[], pick=None)
    if kind == "key":
        it["frame"] = min(max(int(frame or 0), 0), p.data["n"] - 1)
    if kind in STILLS and p.bgswap_on():       # 有「替换背景」图层：默认不带旧背景图
        it["use_bg"] = False
    if kind != "bg":                                      # 标签属于哪个版本，建的时候就记下（新结构里文件夹按它放，取画面前就要知道）
        it["version"] = p.data["current"]
    if kind == "card":
        o = p.obj(oid)
        it.update(oid=oid, label=f"资料卡 · {o['name']}", version=o["version"])
    items(p).append(it)
    if kind != "bg":
        take_base(p, it, log)
    p.save()
    return it


# 图片标签里「蒙版与重绘区域」能改的图层字段：只存在这张图里（it["layer_opts"] = {图层 id: {字段: 值}}），默认继承视频的值，不影响视频
LAYER_OPT_FIELDS = ("edge", "grow", "box_ext", "ext_masks") + MASK_OPTS


def with_layer_opts(p, it):
    """这张图用的项目视图：图层字段换成图片自己改过的值（浅拷贝项目、深拷贝数据，遮罩缓存共用；不存盘）。"""
    lo = it.get("layer_opts") or {}
    if not lo:
        return p
    import copy
    q = copy.copy(p)
    q.data = copy.deepcopy(p.data)
    for o in [v for root in q.data["objects"] for v in [root, *root.get("parts", [])]]:
        ov = lo.get(o["id"]) or {}
        if "box_ext" in ov and "ext_masks" not in ov:               # 旧的单图覆盖只存了 box_ext：用它，不用视频的跟随蒙版
            o.pop("ext_masks", None)
        for k, v in ov.items():
            if k in LAYER_OPT_FIELDS:
                o[k] = v
    return q


def set_layer_opts(p, iid, oids, fields):
    """图片标签里改图层字段：只存进这张图。值和视频一样（或 None）= 去掉覆盖，回到继承视频。"""
    it = get(p, iid)
    lo = it.setdefault("layer_opts", {})
    for oid in oids:
        o = p.obj(oid)
        cur = lo.setdefault(oid, {})
        for k, v in fields.items():
            if k not in LAYER_OPT_FIELDS:
                continue
            if v is None or v == o.get(k):
                cur.pop(k, None)
            else:
                cur[k] = v
        if not cur:
            lo.pop(oid)
    p.save()
    return it


def base_key(p, it):
    """这张参考图的画面取决于什么：视频里全部图层编辑 + 这张图的单图覆盖。变了就要重新取画面。"""
    return json.dumps([edit_sig(p), it.get("layer_opts") or {}], sort_keys=True)


def still(p, it, log=print):
    """首帧 / 尾帧这一帧现在的样子（不写文件）：视频编辑停在这一帧，图层摆放跟视频，蒙版 / 空区 / 重绘区字段用这张图的单图覆盖。
    返回 dict(comp：画面，空区已涂灰 128；zones；people：画面里的人；hole：空区；occ：整块遮挡物；i；rect)。"""
    i = still_index(p, it)
    rect = frame_rect(p)
    H, W = p.data["h"], p.data["w"]
    framed = rect != [0, 0, W, H]
    log(f"{it['label']}：按现在的图层摆放合成第 {i} 帧" + (f"，按画框 {rect[2]}x{rect[3]}（位置 {rect[0]}, {rect[1]}）取" if framed else "") + "…")
    pv = with_layer_opts(p, it)                      # 图片自己改过的蒙版 / 重绘字段（不影响视频）
    if pv is not p:
        log(f"{it['label']}：用这张图自己的蒙版与重绘设置（{len(it['layer_opts'])} 个图层改过）")
    comp, z, L = compose_still(pv, i, layout=True)  # 抠走的地方灰 128 = 空，出候选时 Qwen 才补
    people = np.zeros((H, W), bool)                  # 画面里现在的人（按移动 / 缩放 / 翻转后的位置）
    who, names = np.zeros((H, W), np.uint8), []      # 每个物体占哪里（1, 2, …；生成模式按它写「几个人、各在哪、什么姿势」）
    for o in L.shown:
        m = L.cmask(o["id"], i)                      # 移动过的：不含边缘外扩那一圈（新位置那一圈是空的）
        m = cv2.warpAffine(m.astype(np.uint8), L.Ms[o["id"]], (W, H), flags=cv2.INTER_NEAREST) > 0 if o in L.moved else m
        people |= m
        if m.any() and len(names) < 255:
            names.append(obj_word(o))
            who[m] = len(names)
    # 图片遮罩「整块遮挡物」（只管给 Qwen 的这张图，不影响视频）：移动过的物体，原片里碰到它（3 像素内相接）的别的物体整个
    # 跟着它搬到新位置。女士的左手被挡，是因为放在男士身上某处、那里又被男士自己挡着（1009，2026-11-02 用户定）
    all_i = L.all_i(i)
    occ = np.zeros((H, W), bool)
    for o in L.moved:
        src = L.mask(o["id"], i)
        oth = all_i & ~src
        if not src.any() or not oth.any():
            continue
        _, lab = cv2.connectedComponents(oth.astype(np.uint8))
        hit = np.unique(lab[(cv2.dilate(src.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0) & oth])
        occ |= cv2.warpAffine(np.isin(lab, hit[hit > 0]).astype(np.uint8), L.Ms[o["id"]], (W, H), flags=cv2.INTER_NEAREST) > 0
    if framed:                                       # 画框比原片大（扩图）：原片外 = 完全重绘（和合成用同一个函数）
        occ = to_frame(occ, rect, False)
        comp, z, people = frame_one(L, i, comp, z, rect)   # 原片外也是灰 128
        who = to_frame(who, rect, 0)
    occ &= ~people
    hole = keyframe_hole(z == EMPTY, people)  # 交给 Qwen 补的地方（灰 128）
    comp[hole] = 128
    who[hole] = 0
    return dict(comp=comp, zones=z, people=people, hole=hole, occ=occ, i=i, rect=rect, who=who, names=names)


def set_key_frame(p, it, frame, log=print):
    """关键帧改帧号：重新取画面（候选清空、文件留着；旧候选是另一帧的画面，签名里没有帧号，不清会被当成没过期）。"""
    if it["kind"] != "key":
        raise ValueError("只有关键帧能改帧号")
    it["frame"] = min(max(int(frame), 0), p.data["n"] - 1)
    return take_base(p, it, log)


def card_sys(p, it):
    """资料卡的默认基础提示：人用人物资料卡那句，物体用物体那句（带物体名）。"""
    try:
        word = obj_word(p.obj(it["oid"]))
    except KeyError:                                     # 图层删了：按人物处理
        return PERSON_PROMPT
    return PERSON_PROMPT if word.lower() in PERSON_WORDS else OBJECT_PROMPT.format(name=word)


def take_base(p, it, log=print, keep=False):
    """（重新）取原图：首帧 / 尾帧 = 当前版本按现在的图层摆放合成的那一帧；资料卡 = 物体最清楚的一帧抠到灰底上。
    keep=False（新建 / 手动重取）：候选和选用清空（文件留着）、基础提示换成默认。
    keep=True（出候选前自动取）：候选、选用、基础提示都保留，旧候选靠 cand_sig 判断过期。"""
    d = folder(p, it["id"])

    def reset():                                         # 新画面已经算出来了才清：算失败时这张图保持原样（上传的画面、局部重绘范围都还在）
        it.pop("base_upload", None)                      # 改回从视频取的画面
        it.pop("base_from", None)
        drop_repaint(p, it)                              # 换了画面：局部重绘范围作废
    if it["kind"] in STILLS:
        st = still(p, it, log)
        reset()
        comp, hole, i = st["comp"], st["hole"], st["i"]
        Image.fromarray(np.dstack([comp, np.where(hole, 0, 255).astype(np.uint8)])).save(d / "base.png", compress_level=1)
        Image.fromarray(comp).save(d / "input.png", compress_level=1)
        np.savez_compressed(d / "zones.npz", zones=st["zones"], people=st["people"], hole=hole, occ=st["occ"], who=st["who"])
        it["who"] = st["names"]
        has_bg = PANO.to_frame(p, i, d, it.get("bg_choice")) is not None
        it.update(frame=i, version=it.get("version") or p.data["current"], sig=layer_sig(p), rect=st["rect"], base_key=base_key(p, it))
        if not keep or "use_bg" not in it:
            it["use_bg"] = has_bg
        if not keep or not it.get("sys"):
            it["sys"] = sys_default(it)
    elif it["kind"] == "card":
        o = p.obj(it["oid"])
        frames = p.frames(o["version"])
        canvas, k = card_source(frames, p.masks(o["id"]))
        reset()
        Image.fromarray(canvas).save(d / "base.png", compress_level=1)
        Image.fromarray(canvas).save(d / "input.png", compress_level=1)
        it.update(frame=k, version=o["version"], sys=card_sys(p, it))
        log(f"{it['label']}：取第 {k} 帧（物体够大的帧里最清楚的一帧）")
    it.setdefault("desc", "")                            # 用户写的描述保留（重新取画面不清掉）
    it["base_v"] = uuid.uuid4().hex[:6]                  # 原图换了：界面的图片地址跟着变（不显示浏览器缓存里的旧原图）
    if not keep:
        it["pick"] = None
        it["cands"] = []                                 # 旧候选是按旧原图生成的，对不上了（文件留在文件夹里）
    p.save()
    return it


def ensure_base(p, it, log=print):
    """首帧 / 尾帧出候选前：画面和现在的编辑对不上（或还没取过）就自动重取（不清候选）。"""
    if it["kind"] not in STILLS or it.get("base_upload"):   # 上传的画面：不按视频重取
        return
    zf = folder(p, it["id"]) / "zones.npz"
    if it.get("base_key") == base_key(p, it) and zf.exists():
        if gen_mode(it) != "gen" or "who" in np.load(zf).files:
            return
        log(f"{it['label']}：生成模式要每个物体的位置（老画面没存），重新取画面")   # 画面内容不变，只多存 who
    else:
        log(f"{it['label']}：图层或蒙版设置改过，按现在的样子重新取画面")
    take_base(p, it, log, keep=True)


def keyframe_hole(empty, people):
    """首帧 / 尾帧交给 Qwen 补的范围 = 空的地方本身（物体移走后的洞、缺口、画框外），一个像素都不多加。
    2026-10-31 用户定：不许偷偷外扩（以前这里闭运算 100 + 外扩 30，洞被放大成一大团）。people 参数保留给调用方。"""
    return empty


def keyframe_mask(hole, people, edge=0, feather=0):
    """Qwen 的重绘遮罩（也是拼回的透明度）：hole 里 = 1，外面 = 0。
    feather > 0（蒙版工具「补洞羽化」）：往外 feather 像素慢慢渐变到 0，人身上不渐变（人保持原像素）；以前固定 60，2026-10-31 改成默认 0。
    edge > 0：人身上贴着 hole 的 edge 像素那一圈也 = 1。贴过去的人（移动 / 缩放 / 翻转后）边缘几像素是人和灰 128 混出来的亮色，
    留着不让 Qwen 动，它就顺着画出一圈白色描边（2026-10-29 缩小2，提示词怎么写都去不掉）；盖进 14 像素后白边和残影都没了。"""
    out = hole.astype(np.float32)
    if feather > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * feather + 1, 2 * feather + 1))
        m = cv2.GaussianBlur(cv2.dilate(hole.astype(np.uint8), k).astype(np.float32), (0, 0), max(1.0, feather * 2 / 3))
        pk = cv2.GaussianBlur(cv2.dilate(people.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(np.float32), (0, 0), 3)
        out = np.where(hole, 1.0, m * (1 - pk)).astype(np.float32)
    if edge > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * edge + 1, 2 * edge + 1))
        out[(cv2.dilate(hole.astype(np.uint8), k) > 0) & people] = 1.0
    return out


# 首帧 / 尾帧（和背景图）自己的遮罩处理，只管给 Qwen 的图，不影响视频生成（视频用图层面板里的蒙版选项）。
# 2026-11-01 用户定默认 100 / 30 / 14，界面上可改：透明的人形洞会被 Qwen 画成人（1009 测试 B0 / B1），改成一团就干净（B2 / B3）。
# 2026-11-01：空的地方改成灰 128 + 遮罩外扩 16（img_mgrow）。透明洞 Qwen 2.1 会原样保留成透明（输出 alpha≈0，RGB 是紫色垃圾值，
# 绿幕实测），所以不再用透明；灰洞边会留一圈轮廓线，遮罩往外扩 16 后基本看不出（tests/首帧B多图）。
# 2026-11-03 用户定：补图范围默认全部 0（不外扩、不圆滑），100 / 30 / 14 / 16 只当界面上的「推荐」按钮（IMG_MASK_RECOMMEND）。
IMG_MASK_DEFAULTS = {"img_close": 0, "img_grow": 0, "img_edge": 0, "img_mgrow": 0, "img_occ": 0}   # img_occ：整块遮挡物（0 / 1）
IMG_MASK_RECOMMEND = {"img_close": 100, "img_grow": 30, "img_edge": 14, "img_mgrow": 16}   # 也是背景图补洞的固定值（背景图没有界面）


def image_hole(empty, people, close, grow):
    """图片补洞的范围：空的地方闭运算 close + 外扩 grow 像素（不成人形的一团），去掉画面里的人，再并上空的地方本身。"""
    if not empty.any() or (close <= 0 and grow <= 0):
        return empty
    k = lambda r: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    blob = empty.astype(np.uint8)
    if close > 0:
        blob = cv2.morphologyEx(blob, cv2.MORPH_CLOSE, k(close))
    if grow > 0:
        blob = cv2.dilate(blob, k(grow))
    return ((blob > 0) & ~people) | empty


def region(it, st, o, log):
    """补图范围：取画面时存下的空区（+ 整块遮挡物）→ 整张图圆滑 / 外扩 → 贴人一圈 → 整体外扩。
    st = 取画面的结果（zones.npz 或 still()），o = 图片遮罩选项（IMG_MASK_DEFAULTS 的键）。返回 (补的地方 hole, 遮罩 m：0~1 浮点，也是贴回的透明度)。"""
    zf = st
    z, people = zf["zones"], zf["people"]
    hole0 = zf["hole"] if "hole" in zf else (z == EMPTY) | (z == 4)   # 旧标签没存 hole：就用空区（旧区号里 4 = 接触区）
    if o["img_occ"]:                                 # 整块遮挡物：碰到移动过的物体的别的物体整个算进补图范围（只管这张图）
        if "occ" in zf:
            hole0 = hole0 | zf["occ"]
        else:
            log(f"{it['label']}：「整块遮挡物」要先点「取现在的画面」才有，这次没用上")
    if not hole0.any():
        raise ValueError(f"{it['label']}里没有空的地方要补（图层没动过？）")
    hole = image_hole(hole0, people, o["img_close"], o["img_grow"])
    m = keyframe_mask(hole, people, o["img_edge"])
    if o["img_mgrow"] > 0:                           # 遮罩外扩：盖住 Qwen 照灰洞边画出的轮廓线
        r = o["img_mgrow"]
        m = cv2.dilate(m.astype(np.float32), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    return hole, m


_still_cache = {}


def region_preview(p, it, o, scale):
    """界面叠加层：这张图现在的补图范围，按预览比例缩小，画框大小。返回区号图：2 = 空区（涂灰交给图片模型），
    1 = 补图范围里的其余部分（贴人一圈 / 整体外扩 / 遮挡物，原像素也重绘），0 = 不动。不写文件。"""
    key = (str(p.root), it["id"])
    bk = base_key(p, it)
    hit = _still_cache.get(key)
    if not hit or hit[0] != bk:
        _still_cache[key] = hit = (bk, still(p, it, lambda s: None))
    st = hit[1]
    try:
        hole, m = region(it, st, o, lambda s: None)
    except ValueError:                                   # 没有空的地方：全 0
        hole = np.zeros(st["hole"].shape, bool)
        m = np.zeros(hole.shape, np.float32)
    lab = np.zeros(hole.shape, np.uint8)
    lab[m > 0.5] = 1
    lab[hole] = 2
    h, w = lab.shape
    return cv2.resize(lab, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_NEAREST)


def repaint_path(p, it):
    """局部重绘范围（白 = 重绘）：上传的画面 / 接着编辑的候选图上用户画的，和 base.png 一样大。"""
    return folder(p, it["id"]) / "repaint.png"


def drop_repaint(p, it):
    it.pop("repaint", None)
    repaint_path(p, it).unlink(missing_ok=True)


def set_repaint(p, it, data):
    """存局部重绘范围（请求体 = 蒙版图，任意尺寸，按画面大小缩放）；画的是空的 = 清除。返回范围占画面的比例。"""
    if not it.get("base_upload"):
        raise ValueError("局部重绘只用在上传的画面 / 接着编辑的候选图上")
    h, w = np.array(Image.open(folder(p, it["id"]) / "base.png")).shape[:2]
    m = np.array(Image.open(io.BytesIO(data)).convert("L"))
    if m.shape != (h, w):
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
    m = m > 127
    if not m.any():
        drop_repaint(p, it)
        return 0.0
    cv2.imencode(".png", m.astype(np.uint8) * 255)[1].tofile(str(repaint_path(p, it)))
    it["repaint"] = round(float(m.mean()) * 100, 1)       # 占画面的 %（界面显示）
    return it["repaint"]


REPAINT_FEATHER = 16                                     # 局部重绘羽化默认值（原画面像素，只往范围里面渐变）


def feather_in(m, px):
    """范围里面靠边 px 像素渐变（0 → 1），范围外一律 0：贴回时边上和原图融合，范围外的像素不动。"""
    m = m.astype(np.uint8)
    if px < 1:
        return m.astype(np.float32)
    dist = cv2.distanceTransform(m, cv2.DIST_L2, 5)
    return np.clip(dist / float(px), 0, 1).astype(np.float32)


def load_repaint(p, it):
    """局部重绘范围（bool，和 base.png 一样大）；没有 = None。"""
    f = repaint_path(p, it)
    if not it.get("repaint") or not f.is_file():
        return None
    m = cv2.imdecode(np.fromfile(str(f), np.uint8), cv2.IMREAD_GRAYSCALE)
    return None if m is None else m > 127


def upload_base(p, it, data, log=print):
    """图片编辑里直接上传一张要调整的图，代替从视频取的画面。首帧 / 尾帧按输出画幅大小缩放、没有空区（之后出候选整张按提示词改），
    视频编辑改了也不会自动重取（base_upload）；「改回视频画面」= take_base。资料卡按上传的原尺寸。"""
    img = np.array(open_upload(data))
    it.pop("base_from", None)                            # 上传的图（「用这张继续编辑」会在外面再记上是哪张候选）
    drop_repaint(p, it)                                  # 换了画面：局部重绘范围作废
    d = folder(p, it["id"])
    if it["kind"] in STILLS:
        rect = frame_rect(p)
        if img.shape[:2] != (rect[3], rect[2]):
            img = cv2.resize(img, (rect[2], rect[3]), interpolation=cv2.INTER_AREA)
        z = np.zeros(img.shape[:2], bool)
        np.savez_compressed(d / "zones.npz", zones=z.astype(np.uint8), people=z, hole=z, occ=z)
        it.update(frame=still_index(p, it), version=it.get("version") or p.data["current"], sig=layer_sig(p), rect=rect)
        it["base_upload"] = True
        it["sys"] = sys_for(it, it.get("sys", ""))   # 没改过的默认提示换成「上传的画面」那份
    Image.fromarray(np.dstack([img, np.full(img.shape[:2], 255, np.uint8)])).save(d / "base.png", compress_level=1)
    Image.fromarray(img).save(d / "input.png", compress_level=1)
    it["base_upload"] = True
    it["base_v"] = uuid.uuid4().hex[:6]
    p.save()
    log(f"{it['label']}：用上传的图当画面（{img.shape[1]}x{img.shape[0]}）")
    return it
