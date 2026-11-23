"""参考图的图片生成：按补图范围用图片模型（Qwen Image 2.1）出候选，每出一张存一张。
画面和补图范围来自 refedit，提示词来自 refprompt。
"""
import time
import cv2
import numpy as np
from PIL import Image

import panorama as PANO
from images import STILLS, folder, get, link_cand, next_cand
import mentions as MN
from refedit import IMG_MASK_DEFAULTS, REPAINT_FEATHER, card_sys, ensure_base, feather_in, load_repaint, region
from refprompt import (FILLS, PE_ASK_REPAINT, REPAINT_PROMPT, UPLOAD_PROMPT, body_sentence, fill_words, gen_layout, gen_mode, keyframe_prompt,
                       ref_sentences, sys_for, sys_for_card, with_bg_sentence)

INPAINT_FEATHER = 16    # 只重绘（inpaint）贴回时：洞外一圈羽化多少像素，并先按洞外一圈把颜色对齐（不然补的那块和周围差一点色，边上有印子）


def candidates(p, engine, iid, desc, sys, count, log=print, use_bg=None, fill=None, opts=None, pe=None, body=None, parts=None, seed=None, mode=None,
               mentions=None):
    """Qwen 出几张候选。提示词 = 用户描述 + 系统基础提示（有背景参考时基础提示前加一句背景）。每出一张就存一次（界面一张张显示）。
    首帧 / 尾帧只把要修的范围（空白 + 所有人，外扩一圈）拼回原图，其余保持原像素。
    首帧 / 尾帧有背景全景图时，把这一帧的干净背景当第 2 张图一起给 Qwen（use_bg=False 不给）。"""
    it = get(p, iid)
    ensure_base(p, it, log)                              # 首帧 / 尾帧：用现在的编辑（不用手动「取现在的画面」）
    if mode in ("inpaint", "gen"):                       # 先定模式：基础提示、PE 指令都跟着模式走
        it["mode"] = mode
    it["desc"] = desc.strip()
    it["sys"] = sys_for(it, sys) if it["kind"] in STILLS else sys_for_card(sys, card_sys(p, it))   # 没改过的默认提示换成现在这版
    prompt = it["sys"]
    d = folder(p, iid)
    inp = d / "input.png"
    refs = []
    # 局部重绘（接着编辑的图 / 上传的图上画的范围）：这块涂灰 128 交给 Qwen 重绘，其余原样
    rp = load_repaint(p, it) if it["kind"] in STILLS and it.get("base_upload") else None
    if it["kind"] in STILLS:
        if use_bg is not None:
            it["use_bg"] = bool(use_bg)
        bg = PANO.to_frame(p, it["frame"], d, it.get("bg_choice")) if it.get("use_bg", True) and rp is None else None
        if bg:
            refs = [bg]
            log(f"{it['label']}：背景全景图当第 2 张参考图（{bg}）")
        elif rp is not None:
            log(f"{it['label']}：局部重绘：不带背景图（背景图那句会让 Qwen 把这块画成背景）")
            if prompt.strip() in ("", UPLOAD_PROMPT):        # 没改过的默认基础提示 → 局部重绘那份
                prompt = REPAINT_PROMPT
        elif it.get("use_bg", True):
            log(f"{it['label']}：还没有背景全景图，只用合成图")
        prompt = with_bg_sentence(prompt, bool(refs))
    desc_used = it["desc"]
    if it["kind"] in STILLS and desc_used and desc_used == it.get("desc_vl"):
        # 照测试做法（2026-10-31 用户定「完全一样」）：Qwen-VL 看原片自动写的描述不加（它写的是移动之前的样子，
        # 1009 cand_14 照着把两人画回挨在一起）。用户自己写 / 改过的描述照常加。
        log(f"{it['label']}：「你的描述」是 Qwen-VL 自动写的，没改过，这次不加进提示词")
        desc_used = ""
    # 描述 / 基础提示里的「@名称」→ 这次的图号（mentions.py）：1 = 原图，然后背景图（这次带了才算），然后额外参考图
    keys = ["base"] + ([("bg_local" if it.get("bg_choice") == "local" else "bg")] if refs else []) + ["ref_" + r["id"] for r in it.get("refs", [])]
    fmt = MN.picture_format(engine)
    desc_used = MN.resolve(desc_used, mentions, keys, fmt, log=log)
    prompt = MN.resolve(prompt, mentions, keys, fmt, log=log)
    prompt = " ".join(x for x in (desc_used, prompt) if x)
    if it["kind"] in STILLS:
        if fill in FILLS:
            it["fill"] = fill
        fill = it.get("fill", "grey")
        if fill not in FILLS:                            # 旧标签存的 trans：透明会被 Qwen 原样保留成透明（紫），改用灰
            fill = it["fill"] = "grey"
        if rp is not None:
            fill = "grey"                                # 局部重绘：范围一律涂灰（不改标签里存的选项）
        for k, v in (opts or {}).items():                # 图片自己的遮罩选项（界面上的三个数），存在标签里
            if k in IMG_MASK_DEFAULTS:
                it[k] = max(0, int(v))
        o = {k: int(it.get(k, v)) for k, v in IMG_MASK_DEFAULTS.items()}
        if rp is not None:                               # 局部重绘：只重绘用户画的范围（涂灰 128；原像素留着 Qwen 会照抄原图），其余像素不动
            hole, m = rp, rp.astype(np.float32)
        elif it.get("base_upload"):                      # 上传的画面：没有空区，整张按提示词改
            z = np.load(d / "zones.npz")["hole"]
            hole, m = z, np.zeros(z.shape, np.float32)
        else:
            hole, m = region(it, np.load(d / "zones.npz"), o, log)
        mask = d / "mask.png"
        Image.fromarray((m * 255).astype(np.uint8)).save(mask)
        alpha = m[..., None]
        if rp is not None:                               # 局部重绘：交给 Qwen 的遮罩是硬的；贴回时范围里面靠边那圈羽化，和原图融合
            fe = int(it.get("repaint_feather", REPAINT_FEATHER))
            alpha = feather_in(rp, fe)[..., None]
        rgb = np.array(Image.open(d / "base.png").convert("RGBA"))[..., :3]
        img = rgb.copy()
        img[hole] = 128
        if True:
            if fill == "bg":                             # 预涂背景：空的地方铺这一帧的背景图（没有背景图就报错，不偷偷换别的）
                bgf = PANO.to_frame(p, it["frame"], d, it.get("bg_choice"))
                if not bgf:
                    raise ValueError(f"{it['label']}：选了「预涂背景」但还没有背景图，先到「背景图」标签生成")
                bgi = np.array(Image.open(bgf).convert("RGB"))
                if bgi.shape[:2] != img.shape[:2]:
                    bgi = cv2.resize(bgi, img.shape[1::-1], interpolation=cv2.INTER_AREA)
                img[hole] = bgi[hole]
        inp = d / "qwen_input.png"
        Image.fromarray(img).save(inp)
        base = rgb
        prompt = fill_words(prompt, fill)
        if pe is not None:
            it["pe"] = bool(pe)
        if body is not None:
            it["body"] = bool(body)
        if parts is not None:
            it["body_parts"] = str(parts).strip()           # 补全身体时特别点名的部位（用户填，比如「手」/ hands）
        # 生成：「几个人、各在哪、什么姿势」（Qwen-VL 看每个物体写），交给 PE 一起改写，也加在最终提示词最前面，整张重绘时守住布局
        lay = gen_layout(p, engine, it, d, log) if gen_mode(it) == "gen" else ""
        prompt = keyframe_prompt(p, engine, it, prompt, desc_used, inp, refs, fill, hole, d, log, layout=lay,
                                 ask=PE_ASK_REPAINT if rp is not None else None)
        if it.get("body"):                                # 补全截断的人：最后再钉一句「整个人都在画面里、从头顶到鞋」
            prompt = prompt.rstrip() + " " + body_sentence(it.get("body_parts", ""))
        if lay:
            prompt = lay + " " + prompt
        if rp is not None:
            log(f"{it['label']}：局部重绘：只重绘画的范围（占 {(m > 0.5).mean() * 100:.1f}% 画面，{mask}），羽化 {fe} 像素（只在范围里面），范围外像素不动")
        elif gen_mode(it) != "inpaint":   # 生成（上传的画面也一样）：这一帧（空区涂灰）当参考，Qwen 整张重新画，不接遮罩、不贴回原像素
            mask = alpha = None
            log(f"{it['label']}：生成（整张重新画，不贴回原像素），空的地方 = {dict(trans='透明通道', grey='灰 128', bg='预涂背景图')[fill]}")
        else:
            fe_l = int(it.get("repaint_feather", INPAINT_FEATHER))
            log(f"{it['label']}：只重绘遮罩里（inpaint），空的地方 = {dict(trans='透明通道', grey='灰 128', bg='预涂背景图')[fill]}；"
                f"洞圆滑 {o['img_close']}、洞外扩 {o['img_grow']}、贴人一圈 {o['img_edge']}、遮罩外扩 {o['img_mgrow']} 像素；遮罩占 {(m > 0.5).mean() * 100:.0f}% 画面（{mask}）；"
                f"贴回时往洞外羽化 {fe_l} 像素并先对齐颜色（印子）")
    else:
        fill = None
        alpha = mask = None
        base = np.array(Image.open(inp).convert("RGB"))
    it["prompt"] = prompt
    log(f"{it['label']}：提示词：{prompt}")
    extra = [d / f"ref_{r['id']}.png" for r in it.get("refs", [])]
    if extra:                                            # 额外参考图接在后面：图号 = 1（原图）+ 背景参考 + 第几张
        prompt = prompt + " " + ref_sentences(p, it, 2 + len(refs))
        log(f"{it['label']}：额外参考图 {len(extra)} 张（Picture {2 + len(refs)}–{1 + len(refs) + len(extra)}）")
        log(f"  提示词：{prompt}")
        refs = refs + extra
    h, w = base.shape[:2]
    num = next_cand(p, it)
    base_seed = num if seed is None else int(seed)       # seed：界面填的（第 k 张用 seed + k）；没填 = 候选编号
    for k in range(count):
        name = f"cand_{num + k}"
        log(f"{it['label']}：Qwen 候选 {k + 1}/{count}（seed {base_seed + k}）…")
        resized, saved = False, False
        if hasattr(engine, "edit_image"):
            # 生成模式整张图就是模型输出：直接存成 cand_N.png，不再另存一份内容相同的 _qwen（之前两份逐字节一样）
            out_name = f"{name}.png" if alpha is None else f"{name}_qwen.png"
            q = np.array(Image.open(engine.edit_image(inp, prompt, d, seed=base_seed + k, log=log, out_name=out_name,
                                                      refs=refs, mask=mask)).convert("RGB"))
            if q.shape[:2] != (h, w):
                q = cv2.resize(q, (w, h), interpolation=cv2.INTER_CUBIC)
                resized = True
            saved = alpha is None and not resized          # 文件已经在磁盘上（cand_N.png），不必再写一遍
        else:                                            # 假引擎：只为测界面流程
            q = cv2.GaussianBlur(base, (0, 0), 2 + k)
        if alpha is None:                                # 生成：Qwen 整张画的，直接用
            out = q
        elif rp is not None:                             # 局部重绘：羽化只在画的范围内，范围外一个像素都不动
            out = (base * (1 - alpha) + q * alpha).clip(0, 255).astype(np.uint8)
        else:                                            # 只重绘遮罩：贴回时先按洞外一圈对齐颜色，再往洞外羽化，边界不留印子
            fe = int(it.get("repaint_feather", INPAINT_FEATHER))
            out = PANO.paste_holes(base, q, m > 0.5, feather=fe) if fe > 0 else \
                (base * (1 - alpha) + q * alpha).clip(0, 255).astype(np.uint8)
        if not saved:
            Image.fromarray(out).save(d / f"{name}.png")
        it["cands"].append(name)
        it.setdefault("cand_seed", {})[name] = base_seed + k
        it.setdefault("cand_info", {})[name] = {          # 这张候选是怎么出的（图片结果页显示）
            "time": time.strftime("%Y-%m-%d %H:%M"), "model": getattr(engine, "edit_label", getattr(engine, "qwen_model", "")),
            "mode": "repaint" if it["kind"] in STILLS and rp is not None else gen_mode(it), "fill": fill, "seed": base_seed + k, "pe": bool(it.get("pe", True)),
            "pe_model": getattr(engine, "pe_model", "") if it.get("pe", True) else "",
            "refs": len(refs), "prompt": prompt}
        if it.get("sig"):
            it.setdefault("cand_sig", {})[name] = it["sig"]   # 这张候选是按哪一版图层出的（之后图层改了就标过期）
        link_cand(p, it, name)                            # 结果result/<vN>/图片images/项目名_vN_首帧_K.png
        p.save()
    return it

def api_candidates(p, api, iid, prompt, count, log=print, mentions=None):
    """API 模式（2026-11-15 用户定，和 ComfyUI 那条路完全分开）：直接调平台的图片模型，只给「输入图 + 提示词」。
    输入图 = 第 1 张：这一帧的画面（空的地方涂灰 128）；后面：参考图清单里选着的背景图、额外参考图（顺序和 Picture N 一样）。
    提示词 = 用户写的那段，原样发，软件不加任何句子；不用 PE、seed、遮罩、背景预涂。平台出的整张就是候选（缩回原图大小），不贴回。"""
    it = get(p, iid)
    prompt = (prompt or "").strip()
    if not prompt:
        raise RuntimeError("先写提示词")
    ensure_base(p, it, log)
    it["api_prompt"] = prompt
    d = folder(p, iid)
    src = Image.open(d / "base.png")
    if src.mode in ("RGBA", "LA", "P"):                  # 空的地方是透明的：铺在灰 128 上（透明直接发出去，平台多半画成黑 / 紫）
        src = src.convert("RGBA")
        bg = Image.new("RGBA", src.size, (128, 128, 128, 255))
        src = Image.alpha_composite(bg, src)
    inp = d / "api_input.png"
    src.convert("RGB").save(inp)
    refs, keys = [], ["base"]
    if it["kind"] in STILLS and it.get("use_bg", True) and not it.get("base_upload"):
        bg = PANO.to_frame(p, it["frame"], d, it.get("bg_choice"))
        if bg:
            refs.append(bg)
            keys.append("bg_local" if it.get("bg_choice") == "local" else "bg")
    for r in it.get("refs", []):
        if (d / f"ref_{r['id']}.png").exists():
            refs.append(d / f"ref_{r['id']}.png")
            keys.append("ref_" + r["id"])
    prompt = MN.resolve(prompt, mentions, keys, MN.PICTURE_QWEN, log=log)   # 「@名称」→ Picture N（存进标签的还是原文）
    model = getattr(api, "cfg", {}).get("image_model", "")
    log(f"{it['label']}：API 生成（{model}），输入图 1 张 + 参考图 {len(refs)} 张")
    log(f"  提示词：{prompt}")
    num = next_cand(p, it)
    for k in range(count):
        name = f"cand_{num + k}"
        log(f"{it['label']}：API 候选 {k + 1}/{count}…")
        api.edit_image(inp, prompt, d, log=log, out_name=f"{name}.png", refs=refs)
        it["cands"].append(name)
        it.setdefault("cand_info", {})[name] = {"time": time.strftime("%Y-%m-%d %H:%M"), "model": model, "mode": "api",
                                                 "refs": len(refs), "prompt": prompt}
        if it.get("sig"):
            it.setdefault("cand_sig", {})[name] = it["sig"]
        link_cand(p, it, name)
        p.save()
    return it

