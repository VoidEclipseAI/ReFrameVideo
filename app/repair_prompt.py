"""生成视频的提示词（补瑕疵）：空区分块问 Qwen-VL 该补成什么，格式在 prompt_format.fill_prompt（训练同一套）。"""
import json
from pathlib import Path
import re
import cv2
import numpy as np
from PIL import Image

# 2026-11-05 用户定（repair-v4，试验 tests/补瑕疵按图层/）：空区按「哪个图层造成的」分块（人原位置的洞、边缘外扩那圈、
# 扩大重绘范围的带；画框外 / 其余单独一块），每块单独问一次：左 = 粗剪这一帧（空区灰），右 = 选用的首帧 / 尾帧，
# 两张图上只描这一块的边框，问右图里这块是什么。比旧的「首帧上提亮一块」准（47bd 男士右边那块终于答成树叶和天空），
# 一次问完所有块会偷懒 / 被逼着编（「不要重复」会编出雪、苔藓）。仍要「忽略人」：允许说人时 VLM 会把旁边的人写进去。
# 回答固定格式一行（BACKGROUND: 短语），按规则解析检查，不合格用固定说法。镜头不再问 VL（固定写 static shot）。
Q_PAIR = ("Left: a rough edited film frame; its flat grey area is missing pixels. Right: the finished version of the same frame. "
          "The coloured outline marks the same area in both images. Name the scenery, surfaces or objects that the RIGHT image shows "
          "inside the outline, as if no person were there; ignore any people, hands, arms or clothing.\n"
          "Reply with exactly one line in this format and nothing else:\nBACKGROUND: <2 to 8 lowercase words, a noun phrase>")
# 参考图-首帧 / 尾帧的「填什么」（refprompt.fill_what）还用这句：一张图上这块亮、其余压暗
Q_PART = ("The brightly lit region of this image was just filled in; everything else is darkened. Name the scenery, surfaces "
          "or objects inside the brightly lit region, as if no person were there. Ignore any people, hands, arms or clothing.\n"
          "Reply with exactly one line in this format and nothing else:\n"
          "BACKGROUND: <2 to 8 lowercase words, a noun phrase>")
MIN_FRAC, MAX_HOLES = 0.002, 6
PART_FALLBACK = "the same background as around it"
CAM_FALLBACK = "static shot"    # 镜头固定这么写（不问 VL）
# 补洞描述里不许出现的词：一出现就说明 VL 把人 / 身体写进去了（H3 会照着在空区里画人、画手）
SURFACE_FACE = ("cliff", "rock", "stone", "wall", "building", "mountain", "clock", "brick", "concrete", "glass")
PEOPLE_WORDS = ("person", "people", "man", "men", "woman", "women", "girl", "boy", "figure", "silhouette", "body", "hand",
                "hands", "arm", "arms", "finger", "fingers", "face", "head", "hair", "shoulder", "jacket", "shirt", "coat", "clothing")

def _pf():
    import prompt_format                                 # 和训练 LoRA 同一套句子（app/prompt_format.py）
    return prompt_format


def clean(s):
    """补洞描述：取「BACKGROUND:」后面那句（没写标签就取第一行），去掉各种前缀、引号、句号；
    2–10 个词、只有字母和逗号、不含人 / 身体的词才算合格，否则返回 None（用退路）。"""
    m = re.search(r'BACKGROUND\s*[:：]\s*(.+)', s, flags=re.I)
    s = (m.group(1) if m else s.strip().split('\n')[0]).strip()
    s = re.sub(r'^(answer|response|phrase|background|region|scenery)\s*[:：]\s*', '', s, flags=re.I)
    s = re.sub(r'^(the )?(brightly lit |bright |lit |highlighted )?(region|area)( of the image)? (shows|contains|is|depicts) ', '', s, flags=re.I)
    s = s.strip().strip('.').strip('\'"').strip().lower()
    if re.search(r"[^a-z ,'-]", s) or any(w in s for w in ('too dark', 'cannot', "can't", 'unable', 'not visible', 'unclear')):
        return None                                      # 有数字、符号、句子标点：不像一个名词短语
    words = re.findall(r"[a-z]+(?:[-'][a-z]+)?", s)
    people = [k for k, w in enumerate(words) if w in PEOPLE_WORDS
              and not (w == "face" and k and words[k - 1] in SURFACE_FACE)]   # 「cliff face」「rock face」是景物不是人脸
    if not 2 <= len(words) <= 10 or people:
        return None
    return re.sub(r'\s+', ' ', s).strip(' ,')


def _where(m):
    H, W = m.shape
    ys, xs = np.nonzero(m)
    edge = (ys < H * .1) | (ys >= H * .9) | (xs < W * .1) | (xs >= W * .9)
    if edge.mean() > .8:                                 # 几乎都贴着画面边（扩画幅扩出来的边）：中心点会落在画面中间，说「沿画面边缘」
        return 'along the edges of the frame'
    cx, cy = xs.mean(), ys.mean()
    where = 'on the left side' if cx < W * .38 else 'on the right side' if cx > W * .62 else 'in the centre'
    return where + (' near the top' if cy < H * .3 else ' near the bottom' if cy > H * .7 else '') + ' of the frame'


def hole_regions(zone, keep=None, layout=None, rect=None, i=0):
    """这一帧的空区分块 [(名字, 遮罩)]，按面积从大到小：有布局时按图层分（regions._Layout.holes_by_layer），
    剩下的（画框外等）合成一块；没有布局时按连通块分。去掉贴着人的细圈（边缘外扩那一圈），只留成块的空区。"""
    from regions import EMPTY, frame_rect, to_frame
    empty = zone == EMPTY
    if keep is not None:
        empty &= ~keep
    r = max(2, zone.shape[1] // 120)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    tidy = lambda m: cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, k) > 0
    out = []
    if layout is not None:
        rect = rect or frame_rect(layout.project)
        names = {o["id"]: o["name"] for o in layout.objs}
        for oid, m in layout.holes_by_layer(i).items():
            m = to_frame(m, rect, False) if m.shape != zone.shape else m
            out.append((names.get(oid, oid), tidy(m & empty)))
        taken = np.zeros_like(empty)
        for _, m in out:
            taken |= m
        out.append(("其余空区（画框外等）", tidy(empty & ~taken)))
    else:
        n, lab, st, _ = cv2.connectedComponentsWithStats(cv2.dilate(empty.astype(np.uint8), np.ones((15, 15), np.uint8)))
        out = [(f"区域 {c}", (lab == c) & empty) for c in range(1, n)]
    out = [(nm, m) for nm, m in out if m.mean() >= MIN_FRAC]
    return sorted(out, key=lambda x: -x[1].sum())[:MAX_HOLES]


def pair_image(rough, ref, m, path):
    """左 = 粗剪这一帧（空区灰），右 = 选用的参考帧，两边都只描这一块的边框。"""
    h, w = rough.shape[:2]
    right = cv2.resize(ref, (w, h), interpolation=cv2.INTER_AREA)
    left = rough.copy()
    cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for img in (left, right):
        cv2.drawContours(img, cs, -1, (255, 64, 64), max(3, w // 300))
    pair = np.concatenate([left, np.full((h, max(8, w // 100), 3), 255, np.uint8), right], 1)
    sc = 1600 / pair.shape[1]
    Image.fromarray(cv2.resize(pair, (1600, round(pair.shape[0] * sc)), interpolation=cv2.INTER_AREA)).save(path)
    return path


class _VL:
    """按选的模型看图：name 进 describe 缓存的键（换模型不会拿到别的模型的旧回答）。"""
    def __init__(self, name, fn):
        self.name, self.describe = name, fn


def vl_engine(engine, model):
    """空区分析用哪个模型看图：None / qwen3vl8b = 默认（和以前一样，缓存照用）；其余 = ComfyUI 上的 VL_MODELS。
    （以前还能选云端 API，2026-11-18 去掉：云端只用于图片生成 / 视频生成；旧的 api 当默认。）"""
    if not model or model in ('qwen3vl8b', 'api'):
        return engine
    base = getattr(engine, 'base', engine)
    if model not in getattr(base, 'VL_MODELS', {}):
        raise RuntimeError(f'没有这个空区分析模型：{model}')
    return _VL(f"{getattr(base, 'name', '')}:{model}", lambda img, q, log, *a: base.describe(img, q, log, *a, model=model))


def build(project, engine, work, zones, ends, has_bg, cards, keep, scene, log, composite, layout=None, rect=None, system=None, vid=None,
          vl=True, vl_model=None):
    """vl=False（视频生成面板「空区分析」选了「不用」）：不问 VL，空区用通用补洞描述，其余照常。
    vl_model：用哪个模型看图（vl_engine）。"""
    from engine import check_cancel
    from refprompt import describe_cached
    from regions import EMPTY
    refs = dict(first=any(k=='first' for k,_,_ in ends), last=any(k=='last' for k,_,_ in ends), bg=has_bg, cards=cards)
    holes, answers = [], []
    camera = CAM_FALLBACK
    log('  提示词方案：补瑕疵 v4（空区按图层分块，每块对照粗剪和选用的参考帧问一次）')
    if ends and hasattr(engine, 'describe') and vl:
        engine = vl_engine(engine, vl_model)
        if isinstance(engine, _VL):
            log(f"  空区分析模型：{engine.name}")
        kind, key, i = ends[0]                           # 首帧优先（对第 0 帧），只有尾帧时对最后一帧
        ref = np.array(Image.open(key).convert('RGB'))
        rough = composite[i].copy()
        rough[zones[i] == EMPTY] = 128
        if keep is not None:
            rough[keep] = composite[i][keep]
        Image.fromarray(rough).save(work / 'repair_rough_frame.png')
        regs = hole_regions(zones[i], keep, layout, rect, i)
        mask = np.zeros(zones[i].shape, np.uint8)
        for k, (_, m) in enumerate(regs):
            mask[m] = 40 * (k + 1)
        Image.fromarray(mask).save(work / 'repair_mask.png')
        for k, (name, m) in enumerate(regs):
            check_cancel()
            where = _where(m)
            path = pair_image(rough, ref, m, work / f'repair_region_{k + 1}.png')
            log(f'  补瑕疵：第 {k + 1} 块（{name}，{where}，占画面 {m.mean() * 100:.1f}%）')
            log(f'  [Qwen-VL 输入图片] {path}')
            log('[Qwen-VL 提问]\n' + Q_PAIR)
            raw = describe_cached(project, engine, path, Q_PAIR, log, 30)
            log('[Qwen-VL 原始回答（含缓存结果）]\n' + raw)
            what = clean(raw)
            answers.append(dict(n=k + 1, layer=name, where=where, image=str(path), question=Q_PAIR, answer=raw, parsed=what))
            if not what:
                log(f'  [第 {k + 1} 块] 回答不合格（格式不对 / 写了人或身体 / 无法判断），用固定说法：{PART_FALLBACK}')
                what = PART_FALLBACK
            holes.append(dict(where=where, what=what if what.startswith(('a ', 'an ', 'the ')) else 'the ' + what))
    elif not vl:
        log('  补瑕疵：空区分析已关闭，使用通用补洞描述')
    else:
        log('  补瑕疵：未选首尾帧或当前引擎不支持看图，使用通用补洞描述')
    data = dict(project.data, current=vid) if vid is not None else project.data
    prompt=_pf().fill_prompt(data,refs,holes,camera=camera)
    if (system or '').strip():
        # 自定义系统提示词也必须带上这次实际分析出的补洞描述。
        fills = ' '.join(f"The grey area {h['where']} is {h['what']}." for h in holes)
        prompt = merge_user(system.strip(), fills)
    prompt = merge_user(prompt, scene)
    (work/'repair_analysis.json').write_text(json.dumps(dict(scheme='repair-v4',refs=refs,holes=holes,camera=camera,answers=answers),ensure_ascii=False,indent=2),encoding='utf-8')
    (work/'repair_prompt.txt').write_text(prompt,encoding='utf-8')
    log('[H3 最终提示词 BEGIN]')
    for line in prompt.splitlines(): log(line)
    log('[H3 最终提示词 END]')
    return prompt


def system_prompt(project, first, last, has_bg, cards, vid=None):
    """生成前展示补瑕疵六段模板；逐块 Qwen-VL 描述在生成时加入。"""
    refs = dict(first=first, last=last, bg=has_bg, cards=cards)
    data = dict(project.data, current=vid) if vid is not None else project.data
    return _pf().fill_prompt(data, refs, [], camera=CAM_FALLBACK), refs


USER_MARK = "detailed_description:\n"


def merge_user(prompt, user):
    """用户提示词接在 detailed_description 的 [Shot 1] 后面（训练里 scene 描述的位置）；没有这一段就加在最后。"""
    user = (user or "").strip()
    if not user:
        return prompt
    if user[-1] not in ".!?。！？":
        user += "."
    if USER_MARK + "[Shot 1] " in prompt:
        return prompt.replace(USER_MARK + "[Shot 1] ", USER_MARK + "[Shot 1] " + user + " ", 1)
    if USER_MARK in prompt:
        return prompt.replace(USER_MARK, USER_MARK + user + " ", 1)
    return prompt.rstrip() + "\n\n" + user


def build_train(project, work, ends, has_bg, cards, user, log, vid=None, system=None):
    """生成视频的提示词 = 系统提示词（system 给了就用用户改过的，否则 system_prompt 默认）+ 用户提示词（merge_user）。"""
    first, last = any(k == "first" for k, _, _ in ends), any(k == "last" for k, _, _ in ends)
    default, info = system_prompt(project, first, last, has_bg, cards, vid)
    custom = bool((system or "").strip())
    prompt = merge_user(system.strip() if custom else default, user)
    log("  提示词方案：" + ("用户改过的系统提示词" if custom else "训练句子（prompt_format.build_prompt，和 LoRA 训练同一套；不问 Qwen-VL）")
        + ("；加了用户提示词" if (user or "").strip() else ""))
    (work / 'repair_analysis.json').write_text(json.dumps(dict(scheme='train-v1', custom_system=custom, user=user or "", **info),
                                                          ensure_ascii=False, indent=2), encoding='utf-8')
    (work / 'repair_prompt.txt').write_text(prompt, encoding='utf-8')
    log('[H3 最终提示词 BEGIN]')
    for line in prompt.splitlines():
        log(line)
    log('[H3 最终提示词 END]')
    return prompt
