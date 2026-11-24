"""「导出」：把软件会交给模型的输入整理成一个 zip，用户拿去自己的 ComfyUI 里跑（2026-11-16 用户定）。

每个 zip 里：模型的输入文件（已经预填好：空的地方涂灰 / 背景预填、画布大小、17k+5 帧…）+ prompt.txt
+ workflow_api.json（ComfyUI「导出 (API)」格式，文件名已经填成 zip 里的名字）+ 说明.txt。
zip 用不压缩（ZIP_STORED）：视频 / png 本来就压不动；同时存一份在项目的 导出export/ 里。
"""
import json
import re
import shutil
import time
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

EXPORT_DIR = "导出export"
REF_TAG = {"首帧": "first_frame", "尾帧": "last_frame", "背景图": "background"}

# 旧结构里的文件夹 → 现在的位置（和 Project._migrate_layout 搬的一致），用来找回旧 pass.txt 里记的参考图
_OLD_DIRS = [(("版本", None, "中间文件", "图片"), ("中间文件work", "图片images")),
             (("版本", None, "中间文件", "生成"), ("中间文件work", "生成gen")),
             (("images",), ("中间文件work", "图片images")), (("图片",), ("中间文件work", "图片images")),
             (("passes",), ("中间文件work", "生成gen")),
             (("panorama",), ("背景图bg",)), (("背景图",), ("背景图bg",)),
             (("gen_refs",), ("参考图refs",)), (("参考图上传",), ("参考图refs",))]


def _find_ref(p, s):
    """生成记录里的参考图路径：还在就用；不在（项目挪过地方 / 迁移成新结构前记的）就按项目里的相对路径找回来。"""
    f = Path(s)
    if f.exists():
        return f
    parts = f.parts
    if p.root.name not in parts:
        return f
    rel = parts[len(parts) - 1 - parts[::-1].index(p.root.name) + 1:]
    for old, new in _OLD_DIRS:
        if len(rel) > len(old) and all(o is None or o == r for o, r in zip(old, rel)):
            g = p.root.joinpath(*new, *rel[len(old):])
            if g.exists():
                return g
    g = p.root.joinpath(*rel)
    return g if g.exists() else f


README_VIDEO = """视频生成素材（ReFrameVideo 导出）

这些就是软件这次会交给 MiniMax H3 的全部输入，已经预填好：
  rough_cut.mp4      加噪的底：粗剪（空的地方涂灰 128），已缩到 H3 画布大小、补到 17k+5 帧
  mask.mov           重绘遮罩（白 = 重画），png 编码无损；不要转成 mp4（h264 会把 255 变成 254）
  video1_guide.mp4   <Video 1> 参考视频（粗剪缩到 768 宽，空处涂灰）
  partial_mask.mov   （只有开了「主体重绘强度」才有）本体部分重绘的地方，强度写在 ReframeH3InpaintMask 节点里
  protect_mask.mov   （只有图层勾了「保护脸部」才有）脸的像素，含脸的格子整格不重绘（节点 conflict = protect）
  hole_mask.mov      （和 protect_mask 一起）空区像素，含空区的格子不保护
  picture_N_*.png    参考图，N 就是提示词里的 <Picture N>
  prompt.txt         提示词
  workflow_api.json  ComfyUI 工作流（API 格式），里面的文件名已经填好

怎么用：
  1. 把视频和图片文件拷进 ComfyUI 的 input 文件夹。
  2. ComfyUI 里打开 workflow_api.json（拖进去或菜单「打开」）。
  3. 需要节点包 ComfyUI-Reframe（ReframeH3InpaintMask）；模型和 LoRA 名字是导出时软件设置里的，和你的 ComfyUI 对不上就在节点里换。
  注意：软件里的「颜色校正」是生成完在软件里做的，自己在 ComfyUI 跑出来的结果没有校色。
"""

README_RESULT = """
另外（视频结果导出才有）：
  result.mp4         成片
  original.mp4       原视频（导入时的工作分辨率），对比用，不交给模型
"""

README_API_VIDEO = """视频生成素材（ReFrameVideo 导出 · 云端 API 生成的结果）

这条结果是「视频生成」切到 API 时由云端平台生成的（不是 H3）：
  result.mp4        成片
  original.mp4      原视频（导入时的工作分辨率），对比用
  api_input.mp4     交给平台的粗剪（空的地方涂灰 128），提示词里的 @视频1
  picture_N_*.png   参考图，N 就是提示词里的 @图片N
  prompt.txt        原样发给平台的提示词
  api_request.json  平台、模型、分辨率
"""

README_IMAGE = """改图素材（ReFrameVideo 导出）

  input.png          要改的图：这一帧的画面，空的地方已经涂成灰 128
  mask.png           空的地方（白 = 空处）
  picture_N_*.png    参考图，N 就是提示词里的 Picture N（第 1 张是 input.png）
  prompt.txt         提示词
  workflow_api.json  改图工作流（API 格式），文件名已经填好；模型是导出时软件设置里选的（Qwen Image 2.1 或 MiniMax H3 Image，
                     H3 Image 没有遮罩输入，mask.png 只供参考）

怎么用：图片拷进 ComfyUI 的 input 文件夹，打开 workflow_api.json。用别的模型 / 平台时，照这些图和提示词自己接。
"""


def safe(s):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", str(s)).strip("_")[:40] or "x"


def write_zip(p, stem, files, texts):
    """files: [(zip 里的名字, 本地文件)]；texts: {名字: 字符串}。存到 项目/导出export/<stem>_<时间>.zip，返回路径。"""
    d = p.root / EXPORT_DIR
    d.mkdir(exist_ok=True)
    out = d / f"{stem}_{time.strftime('%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for name, f in files:
            z.write(f, name)
        for name, t in texts.items():
            z.writestr(name, t.encode("utf-8"))
    return out


def ref_names(labels, refs):
    """参考图在 zip 里的名字：picture_<N>_<是什么>.png（N 从 1 开始，和提示词的 <Picture N> 一样）。"""
    out = []
    for k, (lab, f) in enumerate(zip(labels, refs), start=1):
        tag = REF_TAG.get(lab) or ("card_" + safe(lab.split("：", 1)[-1]) if lab.startswith("资料卡")
                                   else "keyframe_" + "".join(c for c in lab if c.isdigit()) if lab.startswith("关键帧") else "ref")
        out.append((f"picture_{k}_{tag}{Path(f).suffix or '.png'}", Path(f)))
    return out


# ---------- 视频编辑：按现在的编辑准备一次生成的素材（不调 H3） ----------
def export_video(p, engine, graph_engine, op, log):
    import generate as G
    x = G.prepare_compose(p, engine, op, log)
    work, ps, guide, W, H = x["work"], x["pass"], x["guide"], x["W"], x["H"]
    mask = x["canvas_mask"] if x["canvas_mask"] is not None else x["mask"]
    info, mpath, _ = graph_engine.write_inpaint_inputs(ps, work, dict(p.data, w=W, h=H), [(mask, 255)], log)
    refs = ref_names(ps.ref_labels, ps.refs[:9])
    graph, _ = graph_engine.build_h3_graph("rough_cut.mp4", "mask.mov", ps.prompt, info["F"], info["fps"], ps.seed,
                                           [n for n, _ in refs], ref_video_names=["video1_guide.mp4"], check_nodes=False,
                                           partial_name="partial_mask.mov" if info.get("partial") else None,
                                           partial_strength=ps.partial[1] if info.get("partial") else 0.0,
                                           pins=[(k, i) for k, i in ps.pins if k < len(refs)],
                                           protect_name="protect_mask.mov" if info.get("protect") else None,
                                           hole_name="hole_mask.mov" if info.get("hole") else None)
    files = [("rough_cut.mp4", work / "h3_input.mp4"), ("mask.mov", mpath), ("video1_guide.mp4", guide), *refs]
    if info.get("partial"):                              # 主体重绘强度 > 0：本体部分重绘的像素 mask
        files.append(("partial_mask.mov", info["partial"]))
    if info.get("protect"):                              # 保护脸部
        files.append(("protect_mask.mov", info["protect"]))
    if info.get("hole"):
        files.append(("hole_mask.mov", info["hole"]))
    out = write_zip(p, p.out_name(x["c"]["base"], "视频生成素材"), files,
                    {"prompt.txt": ps.prompt, "workflow_api.json": json.dumps(graph, ensure_ascii=False, indent=1),
                     "说明.txt": README_VIDEO})
    log(f"导出完成：{out}（H3 画布 {info['W']}x{info['H']}，{info['n']}→{info['F']} 帧，参考图 {len(refs)} 张）")
    return out


# ---------- 视频结果：这次生成的结果 + 当时交给 H3 的素材（从生成文件夹里取，不重算） ----------
def export_result(p, r):
    f = p.root / r["file"]
    d = r.get("dir") or r["id"]                          # 生成文件夹：结果 id = 文件夹名（二采时一采那条记在 dir 里）
    cands = [p.root / Path(r["file"]).parent, p.gen_dir() / d, p.root / "passes" / d]
    if p.v2 and not p.v3:
        cands += [p.version_dir(v["id"]) / "中间文件" / "生成" / d for v in p.data["versions"]]
    work = next((d for d in cands if (d / "comfy_graph.json").exists() or (d / "api_request.json").exists()), None)
    files, texts, missing = [("result.mp4", f)], {}, []
    orig = p.video("v0")                                  # 原视频（导入时的工作分辨率），方便和成片对比
    (files.append(("original.mp4", orig)) if Path(orig).exists() else missing.append("original.mp4"))
    if work is not None and (work / "api_request.json").exists():      # 视频生成走 API 的结果：没有 ComfyUI 工作流
        req = json.loads((work / "api_request.json").read_text(encoding="utf-8"))
        files.append(("api_input.mp4", work / req.get("video", "api_input.mp4")))
        for k, (src, lab) in enumerate(zip(req.get("refs", []), req.get("ref_labels", [])), start=1):
            files.append((f"picture_{k}_{safe(REF_TAG.get(lab, lab))}{Path(src).suffix or '.png'}", _find_ref(p, src)))
        missing = [nm for nm, src in files if not Path(src).exists()]
        files = [(nm, src) for nm, src in files if Path(src).exists()]
        texts = {"prompt.txt": req.get("prompt", ""), "api_request.json": json.dumps(req, ensure_ascii=False, indent=1),
                 "说明.txt": README_API_VIDEO}
    elif work is not None:
        g = json.loads((work / "comfy_graph.json").read_text(encoding="utf-8"))
        txt = (work / "pass.txt").read_text(encoding="utf-8") if (work / "pass.txt").exists() else ""
        m = re.search(r"^refs: \[(.*)\]$", txt, re.M)
        refs = re.findall(r"Path\('([^']+)'\)", m.group(1)) if m else []
        prompt = txt.split("prompt:\n", 1)[1].rsplit("\nrefs:", 1)[0] if "prompt:\n" in txt else ""
        names = {"5": ("rough_cut.mp4", work / "h3_input.mp4"), "7": ("mask.mov", work / "h3_pixel_mask.mov"),
                 "50": ("video1_guide.mp4", work / "guide.mp4"), "70": ("partial_mask.mov", work / "h3_partial_mask.mov"),
                 "73": ("protect_mask.mov", work / "h3_protect_mask.mov"),
                 "76": ("hole_mask.mov", work / "h3_hole_mask.mov")}
        for nid, node in g.items():                       # 工作流里上传到 ComfyUI 的文件名换成 zip 里的名字
            if node["class_type"] == "LoadVideo" and nid in names:
                node["inputs"]["file"] = names[nid][0]
            elif node["class_type"] == "LoadImage" and 30 <= int(nid) < 50 and int(nid) - 30 < len(refs):
                k = int(nid) - 30
                nm = f"picture_{k + 1}{Path(refs[k]).suffix or '.png'}"
                node["inputs"]["image"] = nm
                names[nid] = (nm, _find_ref(p, refs[k]))
        if "70" not in g:                                 # 没开主体重绘强度：没有这个文件
            names.pop("70")
        for k in ("73", "76"):                            # 没开保护脸部
            if k not in g:
                names.pop(k)
        for nm, src in names.values():
            (files.append((nm, src)) if Path(src).exists() else missing.append(nm))
        texts = {"prompt.txt": prompt, "workflow_api.json": json.dumps(g, ensure_ascii=False, indent=1), "说明.txt": README_VIDEO + README_RESULT}
        if (work / "inputused.txt").exists():
            files.append(("inputused.txt", work / "inputused.txt"))
    else:
        missing.append("生成素材（找不到这次生成的文件夹）")
    if missing:
        texts["缺少的文件.txt"] = "这些文件当时没留下来（旧项目或已清理）：\n" + "\n".join(missing)
    return write_zip(p, p.out_name(r.get("base") or "v0", safe(r.get("label", r["id"]))), files, texts), missing


# ---------- 图片编辑：这一帧（空处涂灰）+ 遮罩 + 参考图 + 提示词 ----------
def export_image(p, it, graph_engine, prompt, mode, refs_with_labels):
    import images as IM
    d = IM.folder(p, it["id"])
    src = Image.open(d / "base.png").convert("RGBA")
    a = np.array(src)[..., 3]
    tmp = d / "_export"
    tmp.mkdir(exist_ok=True)
    flat = Image.alpha_composite(Image.new("RGBA", src.size, (128, 128, 128, 255)), src).convert("RGB")
    flat.save(tmp / "input.png")
    Image.fromarray(((a < 128) * 255).astype(np.uint8)).save(tmp / "mask.png")
    refs = [(f"picture_{k}_{safe(lab)}.png", Path(f)) for k, (lab, f) in enumerate(refs_with_labels, start=2)]
    files = [("input.png", tmp / "input.png"), ("mask.png", tmp / "mask.png"), *refs]
    texts = {"prompt.txt": prompt, "说明.txt": README_IMAGE}
    if graph_engine is not None and graph_engine.QWEN_MODELS[graph_engine.qwen_model].get("family") == "h3":   # MiniMax H3 Image：没有遮罩输入
        cw, ch = graph_engine.h3img_canvas(*flat.size)        # 和软件里一样：第 1 张先放到约 4 MP 再交给模型
        flat.resize((cw, ch), Image.LANCZOS).save(tmp / "input.png")
        g, _ = graph_engine.build_h3img_graph(["input.png"] + [n for n, _ in refs], prompt, 42, cw, ch)
        texts["workflow_api.json"] = json.dumps(g, ensure_ascii=False, indent=1)
    elif graph_engine is not None:
        g, _ = graph_engine.build_edit_graph("input.png", prompt, 42, extra_refs=[n for n, _ in refs],
                                             mask_name="mask.png" if mode == "inpaint" else None)
        texts["workflow_api.json"] = json.dumps(g, ensure_ascii=False, indent=1)
    out = write_zip(p, p.out_name(it.get("version") or p.data["current"], safe(IM.KINDS.get(it["kind"], it["kind"])), "改图素材"), files, texts)
    shutil.rmtree(tmp, ignore_errors=True)
    return out


# ---------- 图片结果：选用的（或指定的）候选 + 它的提示词；all = 全部候选 ----------
def export_cands(p, it, names):
    import images as IM
    d = p.image_dir(it)
    files, lines = [], []
    for n in names:
        f = d / f"{n}.png"
        if f.exists():
            files.append((f"{n}.png", f))
            ci = (it.get("cand_info") or {}).get(n) or {}
            lines.append(f"[{n}] {ci.get('time', '')} · {ci.get('model', '')} · seed {ci.get('seed', '')}\n{ci.get('prompt', '')}\n")
    kind = safe(IM.KINDS.get(it["kind"], it["kind"]))
    stem = p.out_name(it.get("version") or p.data["current"], kind, names[0] if len(names) == 1 else "候选")
    return write_zip(p, stem, files, {"prompt.txt": "\n".join(lines)})
