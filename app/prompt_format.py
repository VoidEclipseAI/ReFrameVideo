"""训练 prompt 的写法（H3 Ref2VA 六段格式）。build_dataset.py 打包、rewrite_prompts.py 在训练机器上重写都用它；
软件端生成 prompt 也必须用同一套句子，训练和使用才对得上。

标签：粗剪视频 = <Video 1>，首尾帧 = <Picture 1> / <Picture 2>，
背景图 = <Picture 3> → <Subject 1>（环境），资料卡 = 下一个 Picture → 下一个 Subject。没有的就不写、编号顺延。

修复指令（“粗剪视频需要被怎么修”，不是“造数据时做了什么”——软件让用户点修复标签，所以能写出一模一样的句子）
按官方 ref-en.txt 的编辑任务写法放两处（社区的编辑例子也是两处都写）：
    summary：         [video editing …] The target video is an edited version of <Video 1>. + 修复指令
    detailed_description：[Shot 1] 画面描述 + 修复指令
"""

VIDEO_DEF = ("<Video 1> is the source video for the target video edit: a rough software composite of this shot whose "
             "content, layout and motion the target follows; its flat grey areas are empty.")
VIDEO_KEEP = ("<Video 1> (whole shot): partially_preserved - its content, layout, motion and timing are kept; the empty "
              "grey areas are filled and anything pasted, blurry or mismatched is re-rendered to match the scene.")
# 动作参考视频（motion.mp4，<Video 2>）：SAM 跟踪到的物体按目标位置贴在灰 128 底上，原片动作（软件旧写法的动作参考视频同一种样子）
MOTION_DEF = ("<Video 2> is the motion reference for this shot: the tracked subjects cut out at their positions in the "
              "target video on a flat grey background, moving exactly as they should move.")
MOTION_KEEP = ("<Video 2> (body motion and timing of the subjects): fully_preserved - every pose, gesture, turn and "
               "movement follows <Video 2> frame by frame at the same positions in the frame; its flat grey background "
               "is not part of the target video.")
MOTION_SHOT = "The subjects move exactly as in <Video 2>, with the same poses, speed and timing."

# 修复指令：{s} = 物体名（person、horse……），{r} = 资料卡的 <Subject N>
EDIT = {
    "remove_obj": "fill the empty grey area with the background that belongs there",
    "remove_hole": "fill the empty grey area with the background that belongs there",
    "move": "blend the {s} into its new place and fill the empty grey area it left with the background",
    "fill_object": "rebuild the missing {s} in the empty grey area to match the first and last frames",
    "replace": "put {r} into the empty grey area, following the original motion",
    "replace_noref": "fill the empty grey area with a subject that matches the first and last frames, following the original motion",
    "occlude": "complete the hidden part of the {s}",
    "harmonize": "re-render the pasted {s} so its light, colour, edges and shadow fit the scene",
    "add_cutout": "re-render the added {s} so its light, colour, grain and shadow fit the scene and clean its matte edges",
    "shrink": "fill the empty grey ring around the {s} with the background and blend it in",
    "enlarge": "restore sharp, natural detail on the enlarged {s}",
    "zoom_in": "restore sharp, natural detail across the whole blurry frame",
    "outpaint": "extend the scene into the empty grey borders",
    "recolor": "give the {s} the colours shown in the first and last frames",
    "grade": "match the colour and light of the first and last frames across the whole shot",
    # 变换（移动 / 缩放 / 旋转 / 翻转后贴回去）：融合到新位置和姿态、光照对上，补上原位置露出来的空洞
    "transform": "blend the {s} into its new position, size and orientation so its light, edges and shadow fit the "
                 "scene, and fill the empty grey area it left with the background",
    "shadow_rm": "fill the empty grey area with the background and remove the leftover shadow of the removed {s}",
    "text_hole": "fill the empty grey area left by the removed text or logo with the background that belongs there",
    "retexture": "give the {s} the material, pattern and colour shown in the first and last frames, keeping its shape, "
                 "folds and motion",
    "bg_swap": "re-render the background to match the first and last frames, keeping the subjects and their motion",
    # 换人（保留表演）：粗剪里是另一个人（或另一个东西），姿势、动作、表情照粗剪，长相照资料卡
    "swap": "keep the pose, expression and motion of the {s} in <Video 1> but re-render them as {r}, with {r}'s face, "
            "hair, body and clothing",
    "swap_noref": "keep the pose, expression and motion of the {s} in <Video 1> but re-render them to match the first "
                  "and last frames",
    # 按首尾帧重画（外部数据 CoinVE 反过来用：粗剪里这些区域被改过——加了 / 去了 / 换了东西——要按首尾帧改回来）
    "keyframe_match": "re-render the marked areas so they match the first and last frames, keeping the motion and "
                      "layout of <Video 1>",
    "identity": "keep the selected region as it is",
    "user_shape": "redraw the selected region so it blends in naturally",
}


def edit_line(ops, names, ref_subj):
    """按样本的操作记录写修复指令（每个样本都写）。返回一句话，如 “Fill the empty grey area …; extend the scene ….”"""
    lines = []
    for op in ops:
        k, o = op["op"], op.get("obj")
        if k in ("replace", "swap") and not ref_subj:
            k += "_noref"
        if k not in EDIT:
            continue
        name = names[o] if o is not None and o < len(names) else "subject"
        r = ref_subj
        if isinstance(ref_subj, dict):                    # 多张资料卡（软件用）：{物体名: <Subject N>}，换人 / 替换用这个物体自己的那张
            r = ref_subj.get(name) or next(iter(ref_subj.values()), None)
        t = EDIT[k].format(s=name, r=r)
        if t not in lines:
            lines.append(t)
    if not lines:
        return ""
    text = "; ".join(lines) + "."
    return text[0].upper() + text[1:]


# 给了哪些关键帧：fl = 首帧 + 尾帧，f = 只有首帧，l = 只有尾帧，none = 都没有（参考图从 <Picture 1> 开始编号）
KEY_PHRASE = {"fl": "the first and last frames", "f": "the first frame", "l": "the last frame", "none": "the scene"}
KEY_REAL = {"fl": "<Video 1>, <Picture 1> and <Picture 2>", "f": "<Video 1> and <Picture 1>",
            "l": "<Video 1> and <Picture 1>", "none": "<Video 1>"}


def key_mode(keys):
    return {True: "fl", False: "none"}.get(keys, keys)


def build_prompt(ops, names, has_bg, has_ref, scene, keys=True, motion=False, ref_names=None):
    """keys：True / "fl" = 首尾帧都给；"f" 只给首帧；"l" 只给尾帧；False / "none" 都不给
    （静止图片做成的样本：首尾帧就是答案，给了会被照抄）。修复指令里的 “the first and last frames” 跟着改。
    motion=True：多一个动作参考视频 <Video 2>（motion.mp4）。视频和图片分开编号，<Picture N> 不变。
    ref_names（软件用）：多张资料卡时每张对应的物体名（按图片顺序）。两张以上时每张各占一个 <Subject N> / <Picture N>，
    写明是哪个物体；一张或不给时和训练数据的句子完全一样（由 has_ref 决定）。"""
    mode = key_mode(keys)
    defs, keep, types = [VIDEO_DEF], [VIDEO_KEEP], ["video editing"]
    if motion:
        defs.append(MOTION_DEF)
        keep.append(MOTION_KEEP)
    pic, subj, ref_subj = 1, 1, None
    if mode in ("fl", "f"):
        defs.append("<Picture 1> is the first frame of [Shot 1].")
        keep.append("<Picture 1> ([Shot 1] first frame): fully_preserved - the shot opens on this frame.")
    if mode == "fl":
        defs.append("<Picture 2> is the last frame of [Shot 1].")
        keep.append("<Picture 2> ([Shot 1] last frame): fully_preserved - the shot ends on this frame.")
    if mode == "l":
        defs.append("<Picture 1> is the last frame of [Shot 1].")
        keep.append("<Picture 1> ([Shot 1] last frame): fully_preserved - the shot ends on this frame.")
    if mode != "none":
        types.append("keyframe completion")
        pic = 3 if mode == "fl" else 2
    if has_bg:
        # 背景图只是参考：拼接 + Qwen 去物体 + 整张高清重绘过，细节和真实画面有出入 → partially_preserved，真实画面优先
        defs.append(f"<Subject {subj}> is the environment of this shot from <Picture {pic}>, a repainted background "
                    "plate with the people removed.")
        keep.append(f"<Subject {subj}> (appears in [Shot 1]): partially_preserved - it guides what the empty areas "
                    "behind the subjects should look like; it was repainted, so small details may differ, and the real "
                    f"footage in {KEY_REAL[mode]} takes priority.")
        pic, subj = pic + 1, subj + 1
    if ref_names and len(ref_names) > 1:
        ref_subj = {}
        for nm in ref_names:
            sj = f"<Subject {subj}>"
            ref_subj.setdefault(nm, sj)
            defs.append(f"{sj} is the {nm} shown in the reference sheet <Picture {pic}>.")
            keep.append(f"{sj} (appears in [Shot 1]): fully_preserved - identity, face, hair and clothing are kept.")
            pic, subj = pic + 1, subj + 1
        has_ref = True
    elif has_ref:
        ref_subj = f"<Subject {subj}>"
        defs.append(f"{ref_subj} is the subject shown in the reference sheet <Picture {pic}>.")
        keep.append(f"{ref_subj} (appears in [Shot 1]): fully_preserved - identity, face, hair and clothing are kept.")
    if has_bg or has_ref:
        types.append("reference generation")
    shot = scene.strip()
    if shot and shot[-1] not in ".!?":
        shot += "."
    edit = edit_line(ops, names, ref_subj).replace("the first and last frames", KEY_PHRASE[mode])
    shot = " ".join(x for x in (shot, edit, MOTION_SHOT if motion else "") if x)
    summary = " ".join(x for x in ("The target video is an edited version of <Video 1>.", edit) if x)
    return "\n".join([
        "subject_definitions:", *defs, "",
        "summary:", f"[{' + '.join(types)}] {summary}", "",
        "retention_analysis:", *keep, "",
        "detailed_description:", f"[Shot 1] {shot}", "",
        "overall_soundscape:", "N/A", "",
        "non_diegetic_music:", "N/A",
    ])


def variants(ops, names, has_bg, has_ref, scene, keys=True, motion=False):
    """所有要写的 prompt 文件：{文件名: 内容}。prompt.txt = 打包时有的图；另外几份 = 你删掉背景图 / 资料卡后用的。
    keys=False（没有首尾帧的样本）不写 _noref：没有首尾帧也没有资料卡，就不知道该画成谁了，删资料卡 = 不用这个样本。"""
    out = {"prompt.txt": build_prompt(ops, names, has_bg, has_ref, scene, keys, motion)}
    for b, r in ((False, has_ref), (has_bg, False), (False, False)):
        if (b, r) != (has_bg, has_ref) and (keys or r):
            name = "prompt" + ("" if b or not has_bg else "_nobg") + ("" if r or not has_ref else "_noref") + ".txt"
            out[name] = build_prompt(ops, names, b, r, scene, keys, motion)
    return out


# 软件里的修复标签（生成前让用户点选；每个标签 = EDIT 里的一句）。(键, 界面上显示的名字, 要不要选物体)
# remove_obj 和 remove_hole 句子一样，合成一个“补背景”；replace 没有资料卡时自动换成 replace_noref
TAGS = [
    ("remove_hole", "补背景", False),
    ("move", "移动后融合", True),
    ("transform", "变换后融合（缩放 / 旋转 / 翻转）", True),
    ("shadow_rm", "删除并去掉残留影子", True),
    ("text_hole", "去字幕 / 台标", False),
    ("fill_object", "补回物体", True),
    ("replace", "替换成参考人物", False),
    ("swap", "换人（保留动作表情）", True),
    ("occlude", "补全被挡部分", True),
    ("harmonize", "融合粗贴", True),
    ("add_cutout", "融合外来抠像", True),
    ("shrink", "缩小后补边", True),
    ("enlarge", "放大后变清晰", True),
    ("zoom_in", "整体变清晰", False),
    ("outpaint", "扩展画面", False),
    ("recolor", "物体改色", True),
    ("retexture", "物体换材质 / 花纹", True),
    ("bg_swap", "换背景", False),
    ("grade", "整体调色", False),
    ("keyframe_match", "按首尾帧重画选区", False),
    ("identity", "保持不变", False),
    ("user_shape", "重画选区", False),
]


def prompt_from_tags(tags, has_bg, has_ref, scene):
    """软件用：tags = [(键, 物体名或 None), …] 按用户点选的顺序。返回完整 prompt。"""
    names, ops = [], []
    for key, name in tags:
        if name:
            names.append(name)
            ops.append({"op": key, "obj": len(names) - 1})
        else:
            ops.append({"op": key})
    return build_prompt(ops, names, has_bg, has_ref, scene)


# Promoted repair prompt from validated V2 experiment.
import re

import numpy as np

_repair_MAIN_AREA = 0.02        # 外框面积中位数 ≥ 画面 2% 算主要人物 / 物体；更小的是远处的小人
_repair_MOVE_FRAC = 0.02        # 平移超过画面 2% 算移动
_repair_PRESENT = 0.6           # 出现帧数不到 60% = 只出现一段（比如中途闯入的路人）：单独写一句，不算进「只有 N 个人」
_repair_ORD = ["first", "second", "third", "fourth", "fifth", "sixth"]
_repair_WORDS = {"lady": "woman", "woman": "woman", "girl": "girl", "man": "man", "boy": "boy", "person": "person",
         "people": "person", "child": "child", "kid": "child", "dog": "dog", "cat": "cat", "car": "car"}
_repair_NUM = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def noun(name):
    """图层名 → 英文名词（中文或认不出的一律 person）。"""
    for w in re.findall(r"[a-zA-Z]+", name.lower()):
        if w in _repair_WORDS:
            return _repair_WORDS[w]
    return "person"


def side(cx, w):
    return "left side" if cx < w * 0.38 else "right side" if cx > w * 0.62 else "centre"


def join(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def facts(p, replaced=(), vid=None):
    """从图层记录算出事实（坐标都换到画框里）。replaced 里的隐藏图层 = 换成别的人，不算去掉。
    vid = 哪个版本（排队的生成按点击时的版本；默认当前）。空区蒙版 / 背景预填图层（shape）不是物体，跳过。"""
    W, H = p["w"], p["h"]
    fr = p.get("frame") or {"x": 0, "y": 0, "w": W, "h": H}
    vid = vid or p["current"]
    objs = [o for o in p["objects"] if o["version"] == vid and not o.get("shape")]
    main, tiny, removed, vacated, edits, passing = [], 0, [], [], [], []
    for o in objs:
        bbs = [b for b in o.get("bboxes") or [] if b]
        if not bbs:
            continue
        a = np.array(bbs, float)
        area = float(np.median((a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1]))) / (W * H)
        cx0, cy0 = float(np.median((a[:, 0] + a[:, 2]) / 2)), float(np.median((a[:, 1] + a[:, 3]) / 2))
        t = o.get("tf") or {}
        dx, dy, sx, sy, rot = t.get("x", 0), t.get("y", 0), t.get("sx", 1), t.get("sy", 1), t.get("rot", 0)
        n = noun(o["name"])
        if o["id"] in replaced:                           # 替换：还是一个主要人物，位置不变
            cx = cx0 + dx - fr["x"]
            main.append({"id": o["id"], "noun": "person", "side": side(cx, fr["w"]), "cx": cx, "replaced": True})
            continue
        if not o.get("visible", True):
            removed.append(n)
            vacated.append(side(cx0 - fr["x"], fr["w"]))
            continue
        if area < _repair_MAIN_AREA:
            tiny += 1
            continue
        alive = [i for i, b in enumerate(o.get("bboxes") or []) if b]
        n_all = len(o.get("bboxes") or [])
        if n_all and len(alive) / n_all < _repair_PRESENT:
            when = ("near the end" if alive[0] > n_all * 0.5 else "at the start" if alive[-1] < n_all * 0.5 else
                    "for part of the shot")
            passing.append({"noun": n, "side": side(cx0 + dx - fr["x"], fr["w"]), "when": when})
            continue
        moved = abs(dx) > W * _repair_MOVE_FRAC or abs(dy) > H * _repair_MOVE_FRAC
        cx = cx0 + dx - fr["x"]
        main.append({"id": o["id"], "noun": n, "side": side(cx, fr["w"]), "cx": cx})
        what = []
        if moved:
            what.append("moved to the " + ("left" if dx < 0 else "right") if abs(dx) >= abs(dy) else
                        "moved " + ("up" if dy < 0 else "down"))
            vacated.append(side(cx0 - fr["x"], fr["w"]))
        if abs(abs(sx) - 1) > 0.02 or abs(abs(sy) - 1) > 0.02:
            what.append("made " + ("smaller" if abs(sx) < 1 else "larger"))
        if sx < 0:
            what.append("flipped")
        if abs(rot) > 1:
            what.append("rotated")
        if what:
            edits.append((n, what))
            main[-1]["what"] = what
    main.sort(key=lambda m: m["cx"])
    vacated = [s for s in vacated if s not in {m["side"] for m in main}]   # 那一边现在还站着人：不能说那边是空的
    ext = [s for s, v in (("left", -fr["x"]), ("right", fr["x"] + fr["w"] - W), ("top", -fr["y"]),
                          ("bottom", fr["y"] + fr["h"] - H)) if v > max(W, H) * 0.01]
    return dict(main=main, tiny=tiny, removed=removed, vacated=sorted(set(vacated)), edits=edits, ext=ext, passing=passing)


def fill_prompt(p, refs, holes, camera=None, replaced=()):
    """补瑕疵的写法（2026-10-29 用户定）：对比首帧是为了知道粗剪里每块瑕疵该怎么补，所以提示词只讲「怎么补」——
    <Video 1> 里人、大小、位置、动作都已经对了，照着走；灰的地方是缺的像素，逐块写该补成什么；别的不加。
    不写「和原片比移动了 / 缩小了」（H3 会当成镜头里发生的动作，人慢慢变小）。
    holes = [{"where": "on the left side of the frame", "what": "rows of red cinema seats"}, …]：粗剪第 1 帧的灰块（按连通区域分开），
    每块在首帧上高亮、问 VLM 这里是什么（run_round2.py 里算）。replaced：被替换的图层 id（它的人形灰块不在 holes 里）。"""
    f = facts(p, replaced)
    pics, k = {}, 0
    for role, on in (("first", refs.get("first")), ("last", refs.get("last")), ("bg", refs.get("bg"))):
        if on:
            k += 1
            pics[role] = k
    cards = []
    for oid in refs.get("cards") or []:
        k += 1
        cards.append((oid, k))
    card_of = dict(cards)
    look = f"as shown in <Picture {pics['first']}>" if "first" in pics else \
        f"as shown in <Picture {pics['last']}>" if "last" in pics else "as shown in <Video 1>"

    subs, defs_s, ret_s = 0, [], []
    keys = [(m["noun"], m["side"]) for m in f["main"]]
    for i, m in enumerate(f["main"]):
        subs += 1
        m["sub"] = subs
        if keys.count(keys[i]) > 1:
            m["label"] = f"the {_repair_ORD[i] if i < len(_repair_ORD) else str(i + 1) + 'th'} {m['noun']} from the left"
        else:
            m["label"] = f"the {m['noun']} on the {m['side']}" if m["side"] != "centre" else f"the {m['noun']} in the centre"
        if m.get("replaced"):
            sheet = f"the reference sheet <Picture {card_of[m['id']]}>" if m["id"] in card_of else "the reference sheet"
            defs_s.append(f"<Subject {subs}> is the person on the {m['side'].replace(' side', '')} side of the frame: face, hair, "
                          f"body and clothing exactly as in {sheet}" + (f", standing as in <Picture {pics['first']}>." if "first" in pics else "."))
            ret_s.append(f"<Subject {subs}> (appears in [Shot 1]): fully_preserved - identity, face, hair and clothing from {sheet}.")
            continue
        src = look + (f" and the reference sheet <Picture {card_of[m['id']]}>" if m["id"] in card_of else "")
        defs_s.append(f"<Subject {subs}> is {m['label']} of the frame, {src}.")
        ret_s.append(f"<Subject {subs}> (appears in [Shot 1]): fully_preserved - same face, hair and clothing throughout; "
                     "pose, gestures and motion exactly as in <Video 1>, frame by frame.")   # 2026-11-04 用户：加动作
    for oid, n in cards:
        if not any(m["id"] == oid for m in f["main"]):
            subs += 1
            defs_s.append(f"<Subject {subs}> is the subject shown in the reference sheet <Picture {n}>.")
            ret_s.append(f"<Subject {subs}> (appears in [Shot 1]): fully_preserved - identity, face, hair and clothing are kept.")
    env = None
    if "bg" in pics:
        subs += 1
        env = subs
        defs_s.append(f"<Subject {env}> is the empty background of this shot from <Picture {pics['bg']}>, with the people removed.")
        ret_s.append(f"<Subject {env}> (appears in [Shot 1]): fully_preserved - the background stays the same in every frame.")

    nm = len(f["main"])
    who = (f"the {f['main'][0]['noun']}" if nm == 1 else f"the {_repair_NUM[nm] if nm < len(_repair_NUM) else nm} people") if nm else "the scene"
    rp = [m for m in f["main"] if m.get("replaced")]
    sil = " ".join(f"The grey person-shaped area on the {m['side'].replace(' side', '')} side is <Subject {m['sub']}>, who moves "
                   "exactly like that shape." for m in rp)
    fills = [f"the grey area {h['where']} is {h['what']}" for h in holes]
    fill_s = (join(fills)[0].upper() + join(fills)[1:] + ".") if fills else ""
    ref_bg = f", matching <Subject {env}>" if env else ""

    vid = (f"<Video 1> is a rough composite of this exact shot: the people in it, their size, position and movement are already "
           f"correct and are kept as they are. Its flat grey areas are only missing pixels, not objects or people. {sil}").strip()
    defs = [vid]
    if "first" in pics:
        defs.append(f"<Picture {pics['first']}> is the first frame of [Shot 1], with the grey areas already filled in.")
    if "last" in pics:
        defs.append(f"<Picture {pics['last']}> is the last frame of [Shot 1], with the grey areas already filled in.")
    defs += defs_s

    tags = ["video editing"] + (["keyframe completion"] if pics.keys() & {"first", "last"} else []) + \
           (["reference generation"] if env or cards else [])
    span = ("begins from <Picture %d>" % pics["first"] if "first" in pics else "begins") + \
           (" and ends on <Picture %d>" % pics["last"] if "last" in pics else "")
    shot = f"A {camera} of {who}" if camera else f"A shot of {who}"
    only = (f"There {'is' if nm == 1 else 'are'} only {_repair_NUM[nm] if nm < len(_repair_NUM) else nm} {'person' if nm == 1 else 'people'} "
            "in the foreground." if nm else "")
    summary = (f"[{' + '.join(tags)}] {shot}, exactly as laid out in <Video 1>. The shot {span}. Only the grey areas of <Video 1> are "
               f"filled in{ref_bg}: {fill_s[0].lower() + fill_s[1:] if fill_s else 'they become the surrounding background.'} "
               f"{only}").replace("  ", " ").strip()

    ret = ["<Video 1> (the shot itself): fully_preserved - every person, their size, position and movement stay exactly as in <Video 1>; "
           "only its grey areas are filled in."]
    if "first" in pics:
        ret.append(f"<Picture {pics['first']}> ([Shot 1] first frame): fully_preserved - the shot opens on this frame.")
    if "last" in pics:
        ret.append(f"<Picture {pics['last']}> ([Shot 1] last frame): fully_preserved - the shot ends on this frame.")
    ret += ret_s

    frames_ = ("A fixed camera" if camera and ("static" in camera or "fixed" in camera) else "The camera") + \
        " frames the shot exactly as in <Video 1>."
    each = " ".join(f"<Subject {m['sub']}> stays where and as large as in <Video 1> in every frame, moving as in <Video 1>." for m in f["main"])
    nothing = ("The filled areas contain nothing else: no other person, no figure, no silhouette, no shadow, "
               "no out-of-focus shape in the foreground." if fills else "")   # 和测试 facts_prompt.fill_prompt 逐字一致
    detailed = f"[Shot 1] {frames_} {each} {fill_s} {nothing}".replace("  ", " ").strip()

    return "\n".join(["subject_definitions:", *defs, "", "summary:", summary, "", "retention_analysis:", *ret, "",
                      "detailed_description:", detailed, "", "overall_soundscape:", "N/A", "", "non_diegetic_music:", "N/A"])
