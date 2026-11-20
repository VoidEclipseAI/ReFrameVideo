"""所有结果（顶栏「所有结果」）：把所有项目生成过的视频结果和图片候选列在一起，按文件时间从新到旧。

只读：不新建记录，只扫各项目 project.json 里已经记下的结果和图片候选。
图片默认只列选用的那张（每个图片标签一张、背景图一张），all_cands=True 时列出全部候选。
"""

KIND = {"first": "首帧", "last": "尾帧", "card": "资料卡", "bg": "背景图"}


def _mtime(f):
    try:
        return f.stat().st_mtime
    except OSError:
        return None


def items(p, all_cands=False):
    d = p.data
    base = {"pid": d["id"], "project": d["name"]}
    out = []
    for r in d.get("results", []):
        t = _mtime(p.root / r["file"])
        if t is not None:
            out.append({**base, "type": "video", "id": r["id"], "label": r.get("label") or "", "name": r.get("name") or "",
                        "version": r.get("base"), "w": r.get("w"), "h": r.get("h"), "t": t})
    for it in d.get("images", []):
        if it.get("kind") == "bg":                       # 背景图的候选在全景图文件夹里，下面单独列
            continue
        pick = it.get("pick")
        names = list(it.get("cands", [])) if all_cands else ([pick] if pick else [])
        folder = p.image_dir(it)
        for n in names:
            t = _mtime(folder / f"{n}.png")
            if t is not None:
                out.append({**base, "type": "image", "id": it["id"], "cand": n, "kind": it.get("kind"),
                            "label": it.get("label") or KIND.get(it.get("kind"), "图片"), "picked": n == pick,
                            "version": it.get("version"), "t": t})
    pn = d.get("panorama") or {}
    use = pn.get("use")
    names = list(pn.get("candidates", [])) if all_cands else []
    if use and use not in names:
        names.append(use)
    for n in names:
        t = _mtime(p.panorama_dir() / f"{n}.png")
        if t is not None:
            out.append({**base, "type": "bg", "id": n, "kind": "bg", "label": KIND["bg"], "picked": n == use, "t": t})
    return out


def file_of(p, kind, id_, cand=""):
    """一条结果对应的文件（缩略图 / 播放用）。找不到抛 KeyError。"""
    if kind == "video":
        r = next((r for r in p.data.get("results", []) if r["id"] == id_), None)
        if r is None:
            raise KeyError(id_)
        return p.root / r["file"]
    if kind == "image":
        it = next((it for it in p.data.get("images", []) if it["id"] == id_), None)
        if it is None or cand not in it.get("cands", []):
            raise KeyError(id_)
        return p.image_dir(it) / f"{cand}.png"
    if kind == "bg":
        pn = p.data.get("panorama") or {}
        if id_ not in [*pn.get("candidates", []), pn.get("use")]:
            raise KeyError(id_)
        return p.panorama_dir() / f"{id_}.png"
    raise KeyError(kind)
