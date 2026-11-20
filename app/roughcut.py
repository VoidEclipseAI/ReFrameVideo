"""粗剪：按编辑把图层摆好、空区涂灰 128 得到的一段视频，生成时照着重绘的底稿。

build() 一次算出整段的区号图（regions.py）和粗剪；图层没变时直接用项目 cache/ 里上次的结果。
guide_video() 是交给 H3 的 <Video 1>（粗剪缩到 768 宽，空区涂灰）。
"""
import json
import threading
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from engine import check_cancel
from project import CLEAN_OPTS, write_video
from regions import BODY, EMPTY, MASK_OPTS, REDRAW, TRANS, _Layout, cell_zones, frame_one, frame_rect, redrawn, trans_opts, zone_overlay


def _pmap(fn, n):
    """对 0..n-1 每帧调用 fn，8 个线程一起算（每帧互不相关；OpenCV / numpy 计算时不占 Python 锁，实测快 4 倍）。"""
    import os
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(min(8, os.cpu_count() or 4)) as ex:
        return list(ex.map(fn, range(n)))


PREP_VER = 47                   # 分区 / 粗剪的算法改了就加 1：旧缓存作废


def prep_key(project, op):
    """合成准备（分区 + 粗剪）的签名：图层摆放、遮罩和它们的手动修改、各种外扩、画框、全景图都没变 → 结果一样，直接复用。"""
    import hashlib
    import images as IM
    vids = {v["id"] for v in project.data.get("versions", [])}
    vid = op.get("version") if op.get("version") in vids else project.data["current"]
    objs = [[o["id"], o.get("mask"), o.get("alpha"), o.get("edge"), o.get("grow"), o.get("box_ext"), o.get("ext_masks"), o.get("keep_masks"), o.get("box_margin"), o.get("off"),
             [o.get(k) for k in MASK_OPTS], o.get("parts", []), o.get("edits"), sum((o.get("medits") or {}).values()), o.get("start"), o.get("end"), [o.get(k) for k in CLEAN_OPTS]]
            for o in sorted(project.objects_in(vid), key=lambda o: o["id"])]
    raw = json.dumps([PREP_VER, IM.layer_sig(project, vid), objs, bool((project.data.get("trans") or {}).get("blue")), trans_opts(project)], sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def build(project, op, log):
    """算分区 + 粗剪。图层没变（prep_key 一样）时直接用上次存下的结果（项目 cache/ 里），跳过整段计算。"""
    import shutil
    key = prep_key(project, op)
    cdir = project.cache_dir()
    meta_f = cdir / "prep.json"
    if meta_f.exists():
        meta = json.loads(meta_f.read_text(encoding="utf-8"))
        if meta.get("key") == key and (cdir / "prep_comp.npy").exists() and (cdir / "prep_zones.npy").exists():
            L = _Layout(project, op)
            work = project.new_pass_dir(L.base)
            comp, zones = np.load(cdir / "prep_comp.npy"), np.load(cdir / "prep_zones.npy")
            src = Path(meta.get("work", ""))
            if not src.exists():                         # 旧缓存存的是相对路径 / 老项目迁移前的路径：按项目里的生成文件夹找
                src = project.gen_dir() / src.name
            elif not src.is_absolute():
                src = project.root / src
            for f in ("zones.mp4", "zones_cells.mp4", "zones_mid.png", "zones_cells_mid.png", "composite_mid.png", "zones.npz", "zones.txt"):
                if (src / f).exists():
                    shutil.copy2(src / f, work / f)
            log(f"① 图层和上次合成（{src.name}）一样：直接用上次算好的分区和粗剪，跳过分区和扩画框")
            return dict(work=work, comp=comp, zones=zones, frames=L.frames, shown=L.shown, moved=L.moved, lifted=L.lifted,
                        masks=L.masks, Ms=L.Ms, base=L.base, rect=frame_rect(project), layout=L)
    c = _build(project, op, log)
    cdir.mkdir(exist_ok=True)                            # 当场存（约 2 秒）：之后合成会把首帧 / 尾帧锚点写进同一个数组，不能晚存
    np.save(cdir / "prep_comp.npy", c["comp"])
    np.save(cdir / "prep_zones.npy", c["zones"])
    meta_f.write_text(json.dumps(dict(key=key, work=str(Path(c["work"]).resolve()))), encoding="utf-8")   # 绝对路径：从哪个目录启动都找得到
    return c


def _build(project, op, log):
    """算每帧的空区 / 重绘区 + 粗剪。中间结果都写成能直接看的文件（composite / zones）。"""
    L = _Layout(project, op)
    base_vid, frames, n, h, w = L.base, L.frames, L.n, L.h, L.w
    shown, moved, lifted, masks, Ms = L.shown, L.moved, L.lifted, L.masks, L.Ms
    check_cancel()
    log(f"① 合成：变换 {len(moved)} 个（{'、'.join(o['name'] for o in moved) or '无'}），隐藏 {len(lifted) - len(moved)} 个")
    zones = np.zeros((n, h, w), np.uint8)                # 区号：KEEP / REDRAW / EMPTY / BODY / TRANS（regions.py）
    grey = np.zeros((n, h, w), bool)                     # 粗剪里涂灰 128 的地方
    def zi(i):
        zones[i], grey[i] = L.zones_i(i)
    log(f"  分区 {n} 帧（多线程）…")
    _pmap(zi, n)

    work = project.new_pass_dir(L.base)
    check_cancel()
    log("② 粗剪：人移走后的空洞、缺口、画框外都留灰 128，交给 H3 完全重绘（背景图只当参考图）")
    comp = frames.copy()
    _pmap(lambda i: L.paint_i(comp[i], i, grey[i]), n)
    del grey
    rect = frame_rect(project)
    if rect != [0, 0, w, h]:                             # 扩图：整段换到画框大小生成，原片外完全重绘
        log(f"② 按画框 {rect[2]}x{rect[3]}（位置 {rect[0]}, {rect[1]}）生成：原片外留灰 128、完全重绘")
        fc = np.zeros((n, rect[3], rect[2], 3), np.uint8)
        fz = np.zeros((n, rect[3], rect[2]), np.uint8)
        def fo(i):
            fc[i], fz[i], _ = frame_one(L, i, comp[i], zones[i], rect)
        _pmap(fo, n)
        comp, zones = fc, fz
        n, h, w = zones.shape
    fps = project.data["fps"]

    def viz(comp=comp, zones=zones, n=n):                # 给人看的分区视频：后台写，不耽误 H3 开跑（H3 用不到它们）
        vis = np.stack([zone_overlay(comp[i], zones[i]) for i in range(n)])
        write_video(work / "zones.mp4", vis, fps)
        mid = int(np.argmax(redrawn(zones).reshape(n, -1).sum(1)))
        Image.fromarray(comp[mid]).save(work / "composite_mid.png")
        Image.fromarray(vis[mid]).save(work / "zones_mid.png")
        del vis
        cz = cell_zones(zones)                           # 按格：H3 真正收到的范围
        cvis = np.stack([zone_overlay(comp[i], cz[i]) for i in range(n)])
        write_video(work / "zones_cells.mp4", cvis, fps)
        Image.fromarray(cvis[mid]).save(work / "zones_cells_mid.png")
        np.savez_compressed(work / "zones.npz", zones=zones)
    threading.Thread(target=viz).start()
    tot = max(int(redrawn(zones).sum()), 1)
    log(f"  重绘区里空区占 {(zones == EMPTY).sum() / tot * 100:.0f}%，其余 {(zones == REDRAW).sum() / tot * 100:.0f}% 是重绘（不是空区）；"
        f"过渡 {(zones == TRANS).sum() / tot * 100:.0f}%；本体（不重绘）占画面 {(zones == BODY).mean() * 100:.1f}%")
    (work / "zones.txt").write_text("zones.mp4 = 按像素分区；zones_cells.mp4 = 按潜空间格（H3 真正收到的，一格约 35 像素）\n"
                                    "颜色：红 = 空区（粗剪涂灰，完全重绘）  蓝 = 重绘区里不是空区的部分  浅红 = 过渡（空区往保留区外扩，重绘不涂灰）  绿 = 本体（不重绘）  其余 = 保留区\n", encoding="utf-8")
    log(f"  合成输入：{work / 'composite.mp4'}")
    log(f"  分区图：{work / 'zones.mp4'}（单帧 {work / 'zones_mid.png'}）")
    log(f"  按格分区（H3 真正收到的）：{work / 'zones_cells.mp4'}（单帧 {work / 'zones_cells_mid.png'}）")
    return dict(work=work, comp=comp, zones=zones, frames=frames, shown=shown, moved=moved, lifted=lifted,
                masks=masks, Ms=Ms, base=base_vid, rect=rect, layout=L)


GUIDE_W = 768                  # <Video 1> 的宽度：参考视频要整段编码，原尺寸会把 4090 撑爆


def guide_video(comp, zones, work, fps, keep=None, width=GUIDE_W, name="guide.mp4", crf=12):
    """<Video 1> = 粗剪视频：空区涂成中性灰 128，其余照粗剪。keep = 背景预填过的像素（不涂灰）。
    width / name / crf：视频生成走 API 时用同一套规则出一份原尺寸的（api_input.mp4，见 generate.op_api_video）。"""
    n, h, w = zones.shape
    width = int(width) // 2 * 2
    vh = round(h * width / w) // 2 * 2
    g = np.empty((n, vh, width, 3), np.uint8)
    for i in range(n):
        x = comp[i].copy()
        x[(zones[i] == EMPTY) if keep is None else (zones[i] == EMPTY) & ~keep[i]] = 128
        g[i] = x if (width, vh) == (w, h) else cv2.resize(x, (width, vh), interpolation=cv2.INTER_AREA if width < w else cv2.INTER_CUBIC)
    f = Path(work) / name
    write_video(f, g, fps, crf=crf)
    Image.fromarray(g[len(g) // 2]).save(Path(work) / (Path(name).stem + "_mid.png"))
    return f

