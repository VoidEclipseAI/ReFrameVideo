"""参考图的提示词：给图片模型（Qwen Image 2.1）的基础提示、描述、官方 PE 改写的指令、空区填什么、额外参考图的说明句；
以及 Qwen-VL 看图写描述（按图片内容缓存）。
"""
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from images import OLD_REF_NOTE_CARD, REF_NOTE_CARD, folder, get, ref_list

# 首帧 / 尾帧的系统基础提示：只说「补没画完的地方、其余不动」，不点名任何物体，适用于去除 / 移动 / 缩放 / 扩画框各种情况。
# 想要什么具体内容写在用户描述里（标签上那一段，可以让 Qwen-VL 看图写），两段拼起来发给 Qwen：描述在前，基础提示在后。
# 2026-10-31 删掉了「complete any person or object that is cut off at their edge」：灰洞贴着人形的边时，这句让 Qwen 把移走的人画回来（1009 首帧）。
STILL_PROMPT = ("This is a film frame with empty areas filled with flat grey. Paint only the grey areas so they continue the "
                "surrounding scene naturally. Keep everything else exactly as it is: the people, their faces, clothing and poses, "
                "the objects, the framing, the lighting and the colour grade. Do not add any new people or objects.")
_OLD_STILL = ("This is a film frame with empty areas filled with flat grey. Paint only the grey areas so they continue the "
              "surrounding scene naturally, and complete any person or object that is cut off at their edge. Keep everything "
              "else exactly as it is: the people, their faces, clothing and poses, the objects, the framing, the lighting and "
              "the colour grade. Do not add any new people or objects.")
# 「生成」模式（整张重绘、不贴回原像素）的系统基础提示：Picture 1 是摆好位置的草图，照它画成一张完整的电影画面。
# 编辑多半挪了位置，原来的光对不上新位置：要求按新位置的环境光重新打光（方向 / 颜色 / 软硬 / 高光阴影 / 地上的接触阴影）。
# 整张重绘时 Qwen 会自己重新构图（1009 seed 2 把两人画回中间）；写明几个人、各在哪、什么姿势就稳（GEN_LAYOUT，2026-10-31 测试 4 / 4）。
GEN_PROMPT = ("Picture 1 is a rough edit of a film frame: every person and object is already at its final position and size, and "
              "the empty areas are filled with flat grey. Regenerate it as one complete, natural film frame. Keep every person "
              "exactly where Picture 1 shows them, with the same size, pose, face, hair and clothing, and keep the camera framing "
              "and the colour grade. Relight the people and objects so they sit naturally in this scene: the direction, colour and "
              "softness of the light on their faces, bodies and clothing, their highlights and shadows, and their contact shadows "
              "on the ground must agree with the light of the background right around them and with each other. The grey areas "
              "are empty background, not people: fill every grey area with the background, as if nothing had ever been there. "
              "No outlines, seams or halos around anyone, and do not add any new people or objects.")
# 上传的画面（没有空区）：整张按用户描述改，其余保持
UPLOAD_PROMPT = ("Edit Picture 1 as described. Keep everything else the same: the people, their faces, clothing and poses, "
                 "the objects, the framing, the lighting and the colour grade. Do not add any new people or objects.")
DESCRIBE_SUBJECT = ("Describe the main subject in this image in one sentence: what it is (for a person: man or woman, hair, "
                    "clothing with colours), which way it faces, and the exact pose of the arms and hands. Answer with the sentence only.")
DESCRIBE_KEYFRAME = ("Describe this film frame in one or two sentences: the place and light, and each person in it - clothing with "
                     "colours, hair and pose. Describe only what you see.")
DESCRIBE_CARD = "In one sentence, describe the person or object in this image: clothing with colours and materials, hair, details."
BG_SENTENCE = ("Picture 2 is the clean, empty background of this exact shot and framing: paint every background area, "
               "including the empty grey parts, to match Picture 2 exactly. ")
# 资料卡（人）：原图只用来认人，姿势和表情必须重绘成标准站姿 —— 不这么写 Qwen 会照抄原图的动作和表情
# （2026-11-13 实测：卡片正面还是原片的「抬手指、歪头」）。2026-11-13 改。
PERSON_PROMPT = ("Character reference sheet of this exact same person. Use the input picture only to learn what the person "
                 "looks like: face, hairstyle, body shape, clothing, colours, materials and accessories, gender, age and "
                 "ethnicity. Do NOT copy the pose, hand gestures, head tilt or facial expression from the picture: draw "
                 "every view in a neutral, relaxed standing pose - body square to the viewer, both arms hanging straight "
                 "down at the sides, hands still, head level, mouth closed, calm neutral expression. Three full-body views "
                 "side by side on one plain light grey studio background with soft even lighting, same height and same "
                 "scale: front view, side profile view and back view. Full body from head to feet, nothing cropped. Where a "
                 "part of the body is not visible, complete it plausibly in the same style. No text, no labels, no other people.")
_OLD_PERSON_PROMPT = ("Turn this into a clean character reference sheet of this exact same person: three full-body views "
                      "side by side - front view, side profile view and back view - standing in a relaxed neutral pose, "
                      "on a plain light grey studio background with soft even lighting. Keep exactly the same face, hairstyle, "
                      "body shape, clothing, colours, materials and accessories, and the same gender, age and ethnicity. "
                      "Where a part of the body is not visible, complete it plausibly in the same style. No text, no labels, no other people.")
OBJECT_PROMPT = ("Turn this into a clean reference sheet of this exact same {name}: three views side by side - front view, "
                 "side view and back view - on a plain light grey studio background with soft even lighting. Keep exactly "
                 "the same shape, proportions, colours, materials, markings and details. No text, no labels, no people.")
PERSON_WORDS = {"person", "man", "woman", "lady", "girl", "boy", "child", "kid", "people", "guy", "gentleman", "actor", "actress"}


def describe_cached(project, engine, img, prompt, log, *a):
    """看图写描述：同一张图（按内容）+ 同一个问题 + 同一个看图模型 + 同样的长度上限，上次写过就直接用（存在项目 cache/describe.json）。
    看图模型用 describe_id（repair_prompt._VL 带模型名），没有就用 name。"""
    import hashlib
    f = project.cache_dir() / "describe.json"
    who = getattr(engine, "describe_id", None) or getattr(engine, "name", "")
    key = hashlib.sha1(Path(img).read_bytes() + prompt.encode() + str(who).encode() + repr(a).encode()).hexdigest()
    db = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    if db.get(key):
        return db[key]
    out = engine.describe(img, prompt, log, *a)
    if out:
        f.parent.mkdir(exist_ok=True)
        db = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        db[key] = out
        f.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
# 官方 PE 改写（engine.rewrite_prompt）用的一句话指令：只说要做什么，每块空的地方补什么由 PE 看图写（2026-11-02 测 17 张首帧比固定基础提示稳）
PE_ASK = ("Fill the flat grey areas with the background so they continue the surrounding scene naturally, as if nothing had "
          "ever been there. Do not add any people. Keep everything else in the image exactly as it is.")
PE_ASK_GEN = ("Regenerate this rough edit as one complete, natural film frame: keep every person exactly where they are, with "
              "the same size, pose, face, hair and clothing, the same framing and colour grade. Relight them so the light on each "
              "matches the scene right around them: light direction, colour and softness, highlights, shadows and contact shadows "
              "on the ground. The flat grey areas are empty background, not people: fill them with the background as if nothing "
              "had ever been there. Do not add any people.")
PE_ASK_UPLOAD = "Apply the described edit to this image. Keep everything else exactly as it is. Do not add any people."
PE_ASK_BG = " <image2> is the empty background of this exact shot: use it for what belongs in the grey areas."
# 局部重绘（2026-11-11）：用户在接着编辑的图上圈的范围涂灰 128 交给 Qwen（原像素留着的话 Qwen 照抄原图，等于没改）
REPAINT_PROMPT = ("Repaint only the flat grey area in Picture 1 as described, so it blends seamlessly into the image around it: "
                  "continue the shapes, lighting, colour and texture of its surroundings. Keep everything outside the grey area exactly the same.")
PE_ASK_REPAINT = ("Only the flat grey area of this image needs to be painted: paint it as described so it continues the image around "
                  "it seamlessly (shapes, lighting, colour and texture). Keep everything outside the grey area exactly as it is. "
                  "Do not add any new people.")
# 「补全被截断的人」：被灰色截断的人补全身体。身体要往哪边伸，那边得在补图范围里（用重绘区外扩把那边拉开，1009 测试）
PE_BODY = (" Every person whose body is cut off by the grey areas must be completed: draw their missing body, arms, hands and "
           "clothing into the grey areas next to them so each person is whole and natural. Do not add any new people.")
PE_PARTS = " In particular, make sure their {parts} are complete, natural and clearly drawn."
# 勾了「补全截断的人」时追加在提示词最后（PE 之后）：不写成「画一个全身站着的人」，模型会把画框下沿当成画面结束，
# 只补一小截、手套/鞋干脆不画（2026-11-13 实测：同一张 qwen_input、同一 seed，加这句后全身 + 白手套都对）。
BODY_FULL = ("The person is drawn full length in this frame: the whole body from the top of the head down to the shoes "
             "stands on the ground, with the arms hanging at the sides, and every part of the body and clothing that the "
             "picture does not show is drawn complete and clearly visible.")
BODY_PARTS_FULL = " In particular the {parts} are complete, natural and clearly visible."


def body_sentence(parts=""):
    """「补全截断的人」的收尾句（用户可以再点名部位）。"""
    s = BODY_FULL
    bp = " ".join((parts or "").split()).strip()
    if bp:
        s += BODY_PARTS_FULL.format(parts=bp)
    return s   # 用户填的部位
FILL_NOUN = {"trans": "transparent areas", "grey": "flat grey areas", "bg": "pre-filled areas"}


def fill_what(p, engine, bg, hole, d, log):
    """Qwen-VL 看背景图：洞那块亮、其余压暗，问是什么景物（忽略人），同 repair_prompt 的 Q_PART。"""
    from repair_prompt import Q_PART, clean
    b = np.array(Image.open(bg).convert("RGB"))
    if b.shape[:2] != hole.shape:
        b = cv2.resize(b, hole.shape[::-1], interpolation=cv2.INTER_AREA)
    hi = (b * 0.15).astype(np.uint8)
    hi[hole] = b[hole]
    f = d / "fill_what.png"
    Image.fromarray(hi).save(f)
    what = clean(describe_cached(p, engine, f, Q_PART, log, 30) or "")
    log(f"  空的地方填什么（Qwen-VL 看背景图）：{what or '（没回答出来，不加）'}")
    return what


FILLS = ("grey", "bg")   # 首帧 / 尾帧空的地方怎么交给 Qwen：灰 128 / 旧标签存的 "trans"（透明）当灰处理 /
                                 # 预涂背景（空的地方先铺这一帧的背景图 bg.png，存 input_bg.png）。用户在标签里选


def gen_mode(it):
    """首帧 / 尾帧这次怎么出：upload = 上传的画面整张改；gen = 生成（整张重绘）；inpaint = 填补（只重绘补图范围）。"""
    return "upload" if it.get("base_upload") else "inpaint" if it.get("mode") == "inpaint" else "gen"   # 默认生成，填补是辅助


def sys_for_card(sys, default=PERSON_PROMPT):
    """资料卡的基础提示：空着或还是老默认（照抄原图动作的那版）就换成现在的默认（人 / 物体各自的，default）；用户自己改过的照用。"""
    s = (sys or "").strip()
    return default if s in ("", _OLD_PERSON_PROMPT) else s


def sys_default(it):
    """系统基础提示的默认值，跟着模式走。"""
    return {"upload": UPLOAD_PROMPT, "gen": GEN_PROMPT}.get(gen_mode(it), STILL_PROMPT)


def sys_for(it, sys):
    """用户没改过的默认基础提示（不管是哪个模式的）换成现在这个模式的；改过的照用。"""
    return sys_default(it) if sys.strip() in ("", STILL_PROMPT, _OLD_STILL, GEN_PROMPT, UPLOAD_PROMPT) else sys


def gen_layout(p, engine, it, d, log):
    """生成模式：按这一帧每个物体的位置（zones.npz 的 who）写「几个人、各在哪、什么姿势」。
    位置按画面里的横向位置（左 / 中 / 右）算；样子和姿势让 Qwen-VL 看这个物体抠到灰底上的那块写（按图片内容缓存）。"""
    zf = np.load(d / "zones.npz")
    names = it.get("who") or []
    if "who" not in zf.files or not names or not hasattr(engine, "describe"):
        return ""
    who = zf["who"]
    img = np.array(Image.open(d / "input.png").convert("RGB"))
    H, W = who.shape
    subs = []
    for k, name in enumerate(names, 1):
        m = who == k
        if m.sum() < 0.002 * H * W:                   # 太小的（几乎被挡住 / 出画）不写
            continue
        ys, xs = np.nonzero(m)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        pad = int(0.08 * max(y1 - y0, x1 - x0))
        y0, y1, x0, x1 = max(0, y0 - pad), min(H, y1 + pad), max(0, x0 - pad), min(W, x1 + pad)
        crop = np.where(m[..., None], img, 128)[y0:y1, x0:x1].astype(np.uint8)
        f = d / f"who_{k}.png"
        Image.fromarray(crop).save(f)
        ans = ((describe_cached(p, engine, f, DESCRIBE_SUBJECT, log, 120) or "").strip().splitlines() or [""])[0].rstrip(".")
        if not ans:
            continue
        cx = xs.mean() / W
        where = "on the left side" if cx < 0.38 else "on the right side" if cx > 0.62 else "in the centre"
        subs.append((cx, where, ans, name.lower() in PERSON_WORDS or any(w in name.lower().split() for w in PERSON_WORDS)))
        log(f"{it['label']}：{name} 在{dict(zip(('on the left side', 'in the centre', 'on the right side'), ('左边', '中间', '右边')))[where]} | {ans}")
    if not subs:
        return ""
    subs.sort()
    n = len(subs)
    noun = "people" if all(s[3] for s in subs) else "subjects"
    num = f"one {noun[:-1] if noun == 'subjects' else 'person'}" if n == 1 else f"exactly {n} {noun}"
    desc = " ".join(f"{a[0].upper() + a[1:]}, {w} of the frame." for _, w, a, _ in subs)
    return (f"Picture 1 shows {num}. {desc} All of them must stay in the picture, exactly where Picture 1 shows them, with "
            f"exactly these poses. There are only these {n} {noun}; do not add anyone else.")


def fill_words(prompt, fill):
    """把提示词里「灰色的空地方」的说法换成对应的（只换默认句子里的固定说法，用户自己写的不动）。"""
    words = {"trans": (("empty areas filled with flat grey", "empty, transparent areas"),
                       ("Paint only the grey areas", "Paint only the transparent areas"),
                       ("empty grey parts", "empty transparent parts")),
             "bg": (("empty areas filled with flat grey", "areas roughly pre-filled with the background"),
                    ("Paint only the grey areas so they continue", "Repaint those pre-filled areas so they continue"),
                    ("including the empty grey parts", "including the pre-filled parts"),
                    ("the empty areas are filled with flat grey", "the empty areas are roughly pre-filled with the background"),
                    ("Fill every grey area with the background", "Repaint those pre-filled areas"))}.get(fill, ())
    for a, b in words:
        prompt = prompt.replace(a, b)
    return prompt


def with_bg_sentence(prompt, use):
    """提示词里加上 / 去掉“Picture 2 是背景”这一句，和实际有没有发背景图保持一致。"""
    prompt = prompt.replace(BG_SENTENCE, "")
    return (BG_SENTENCE + prompt) if use else prompt


def ref_sentences(p, it, first_n):
    """额外参考图的说明句，图号从 first_n 开始。
    老项目里存的资料卡说明句（「照这张穿衣服」那版）在这里换成现在的衣着参考句，不用用户把参考图删了重加。"""
    out = []
    for k, r in enumerate(ref_list(it)):
        note = (r.get("note") or "").strip()
        if (note.startswith(OLD_REF_NOTE_CARD) and ": dress the " in note
                and note.endswith("exactly as shown in it.")):   # 只换老默认句，用户自己改过的不动
            src = get(p, r["from_iid"]) if r.get("from_iid") else None
            if src and src.get("kind") == "card":
                from project import obj_word
                note = REF_NOTE_CARD.replace("{word}", obj_word(p.obj(src["oid"])))
        if note:
            out.append(note.replace("{n}", str(first_n + k)))
    return " ".join(out)


def describe(p, engine, iid, log=print):
    """Qwen-VL 看这个标签的原图写一段描述，填进用户描述。"""
    it = get(p, iid)
    d = folder(p, iid)
    q = DESCRIBE_CARD if it["kind"] == "card" else DESCRIBE_KEYFRAME
    img = d / "input.png"
    if it["kind"] in ("first", "last"):                  # 看原片这一帧（干净的），不看粗合成：粗合成里的模糊残影会被写成
        img = d / "orig.png"                             # “后面还有个模糊的人”，Qwen 照着把残影留下来（第 5 段）
        Image.fromarray(p.frame(it.get("version", p.data["current"]), it["frame"])).save(img)
    it["desc"] = (engine.describe(img, q, log, 120) or "").strip()
    it["desc_vl"] = it["desc"]                           # 记住这段是 Qwen-VL 自动写的（首帧 / 尾帧出候选时不加，见 candidates）
    p.save()
    log(f"{it['label']}：描述 = {it['desc']}")
    return it


def keyframe_prompt(p, engine, it, prompt, desc_used, inp, refs, fill, hole, d, log, layout="", ask=None):
    """首帧 / 尾帧最后交给图片模型的提示词：灰 128 时先用官方 PE 看图改写（描述 + 一句指令 + 补全被截断的人 / 部位）；
    不用 PE（或没写出来）且有背景图时，Qwen-VL 看背景图里补的那块，加一句「空的地方填什么」。"""
    use_pe = it.get("pe", True) and fill == "grey" and hasattr(engine, "rewrite_prompt")
    if use_pe:                                       # 官方 PE 看图改写：用户描述 + 一句话指令（+ 补全身体）→ 一段详细的编辑指令
        bp = it.get("body_parts", "") if it.get("body") else ""
        mode = gen_mode(it)
        base_ask = ask or {"gen": PE_ASK_GEN, "upload": PE_ASK_UPLOAD}.get(mode, PE_ASK)   # ask：局部重绘的指令
        ask = " ".join(x for x in (layout, desc_used, base_ask + (PE_ASK_BG if refs else "") + (PE_BODY if it.get("body") else "")
                                   + (PE_PARTS.format(parts=bp) if bp else "")) if x)
        log(f"{it['label']}：官方 PE 改写提示词（{dict(gen='生成', upload='上传的画面', inpaint='填补')[mode]}，{'补全被截断的人' if it.get('body') else '只补背景'}）…")
        pr = engine.rewrite_prompt([inp] + refs, ask, log)
        if pr:
            prompt = pr
            it["pe_prompt"] = pr
        else:
            use_pe = False
            log(f"{it['label']}：PE 没写出来，改用系统基础提示")
    if not use_pe and refs and hasattr(engine, "describe"):         # 有背景图：Qwen-VL 看背景图里洞那块写景物（忽略人），加一句「空的地方填什么」
        what = fill_what(p, engine, Path(refs[0]), hole, d, log)
        if what:
            prompt += f" The {FILL_NOUN[fill]} are empty background: fill them with {what}, with nobody there."
    return prompt
