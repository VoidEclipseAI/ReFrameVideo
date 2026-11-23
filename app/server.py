"""视频 PS —— 后端。

运行：
    python app/server.py                                    # 假引擎，只测界面
    python app/server.py --comfy http://<ComfyUI 机器>:8188  # 调用 ComfyUI；不带 --comfy / VE_COMFY = 假引擎（测试都靠这个，别改成自动读设置）
然后浏览器打开 http://127.0.0.1:8765

浏览器只拿 jpg/png，从不直接请求 mp4（避免被 IDM 拦截）。
"""
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
import zlib
from pathlib import Path

import cv2
import numpy as np
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

import workflows as WF
import exporter as EXP
import api_engine as APIE
from api_engine import DEFAULTS as API_DEFAULTS, WithApi, save_key as save_api_key
from engine import ComfyEngine, MockEngine, request_cancel
import engine as ENG
import images as IM
import mentions as MN
import refedit as RE
import refgen as RG
import refprompt as RP
import jobs
import panorama as PANO
import gallery as GAL
import sequence as SEQ
from generate import run_op
from regions import MASK_OPTS, frame_rect, layer_matrix, zone_overlay_map, cell_overlay_map, is_identity
from plate import pano_extent, reframe
import project as PROJ
from project import Project, write_video, FFMPEG, FFPROBE

for _s in (sys.stdout, sys.stderr):                       # 中文 Windows 控制台是 GBK：打印不出的字符用 ? 代替，不让任务因为打印日志而中断
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

APP_DIR = Path(__file__).parent
PROJECTS = Path(os.environ.get("VE_PROJECTS", APP_DIR.parent / "projects"))
if "--projects" in sys.argv:                               # 换一个项目目录（测试用，不碰正式项目）
    PROJECTS = Path(sys.argv[sys.argv.index("--projects") + 1])
PROJECTS.mkdir(exist_ok=True)
SETTINGS = APP_DIR.parent / "app_settings.json"            # 界面上的选择（Qwen 模型、接入方式）记在这里，重启后还在；不存 API key（不进 git）
_comfy = os.environ.get("VE_COMFY")
if "--comfy" in sys.argv:
    _comfy = sys.argv[sys.argv.index("--comfy") + 1]
BASE = ComfyEngine(_comfy) if _comfy else MockEngine()


def read_settings():
    return json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}


def write_settings(s):
    PROJ.write_json_atomic(SETTINGS, s)


def _model_files(st):
    """设置里的 model_files：只留认识的槽位、非空的文件名。"""
    mf = st.get("model_files") or {}
    return {k: str(v) for k, v in mf.items() if k in ComfyEngine.FILE_SLOTS and v} if isinstance(mf, dict) else {}


if isinstance(BASE, ComfyEngine):
    _m = read_settings().get("qwen_model")
    if _m in BASE.QWEN_MODELS:
        BASE.qwen_model = _m
    _pe = read_settings().get("pe_model")                    # PE 没单独选过 = 跟着改图模型
    BASE.pe_model = _pe if _pe in BASE.PE_MODELS else BASE.QWEN_MODELS[BASE.qwen_model].get("pe", "qwen")
    if read_settings().get("image_workflow") in WF.names():    # 上次选的是自己的改图工作流（文件还在才用）
        BASE.image_workflow = read_settings()["image_workflow"]
    BASE.loras = read_settings().get("h3_loras", [])
    BASE.unet = read_settings().get("h3_model") or BASE.unet
    BASE.H3_STEPS = int(read_settings().get("h3_steps") or BASE.H3_STEPS)      # H3 采样步数（视频生成面板）
    BASE.SAM_CKPT = read_settings().get("sam_model") or BASE.SAM_CKPT    # 识别模型（识别物体面板里选）
    BASE.files = _model_files(read_settings())               # 「模型文件」页选的量化版本
_pw = int(read_settings().get("preview_w", PROJ.PROXY_W))      # 画布预览宽度（设置里选）
if _pw in PROJ.PREVIEW_CHOICES:
    PROJ.PREVIEW["w"] = _pw
try:                                                          # H3 画布分辨率（视频生成面板里选；假引擎也照这个算分区叠加层）
    _st = read_settings()
    ENG.set_h3_short(_st.get("h3_short") or ENG.H3_SHORT)
    if _st.get("h3_preset"):
        ENG.set_h3_preset(_st["h3_preset"])
    ENG.set_h3_mode(_st.get("h3_mode") or ENG.H3_MODE)        # 旧的 auto 读进来 = 768p 预设
    if _st.get("h3_free"):
        ENG.set_h3_free(*_st["h3_free"])
except ValueError:
    pass
def _migrate_key():
    """旧界面让填「API key 环境变量名」，有人把 key 本身填进去了（存进了 app_settings.json）：挪进 api_keys.local.json，设置里换回默认变量名。"""
    s = read_settings()
    c = s.get("api") or {}
    v = str(c.get("key_env") or "")
    if v and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v):
        try:
            save_api_key(c.get("provider") or "openai", v)
        except RuntimeError as e:
            print(f"  没能把设置里的 API key 挪走：{e}", flush=True)
            return
        c.pop("key_env", None)
        s["api"] = c
        write_settings(s)
        print("  已把设置里填的 API key 挪到 api_keys.local.json", flush=True)


def _api_settings():
    """读设置里的云端 API（cfg, profiles）。旧版内置的平台（OpenAI / 火山方舟 / APIMart）去掉了：用到过的转成用户自己的平台
    （同 id，key 照用）。只在内存里转，不在启动时写文件（测试导入 server 时不能碰真设置）；下次存云端设置时一起写进去。"""
    old = read_settings().get("api") or {}
    cfg, profiles, _ = APIE.migrate({k: v for k, v in old.items() if k != "profiles"}, old.get("profiles"), APIE.read_keys())
    return cfg, profiles


def _need_api():
    """API 模式提交前：没设平台 / 没填 key 直接 400（中文原因）。"""
    try:
        ENGINE.api._headers()
    except RuntimeError as e:
        raise HTTPException(400, str(e))


def _save_api(c):
    ENGINE.configure(c)
    s = read_settings()
    s["api"] = {**ENGINE.cfg, "profiles": dict(APIE.CUSTOM)}
    write_settings(s)


_migrate_key()
_api_cfg, _api_profiles = _api_settings()
APIE.CUSTOM.update(_api_profiles)                             # 用户的云端平台配置
ENGINE = WithApi(BASE, _api_cfg)                              # 图片 / 视频生成切到 API 时用 ENGINE.api，其余走 ComfyUI

app = FastAPI()

# ---------- 只给本机页面用：防 DNS 重绑定 / 跨站请求 ----------
# 服务只监听 127.0.0.1，但别的网站可以把自己的域名解析到 127.0.0.1 来调接口（DNS 重绑定），或从页面里直接 POST 过来。
# Host 必须是本机名；改东西的请求（非 GET）如果带了别的来源（Origin / Sec-Fetch-Site: cross-site）一律拒绝。
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1", "testserver"}   # testserver = FastAPI TestClient


def _host_of(h):
    """Host 头 → 主机名（去掉端口）：127.0.0.1:8765 → 127.0.0.1，[::1]:8765 → [::1]。"""
    h = (h or "").strip().lower()
    if h.startswith("["):
        return h.split("]")[0] + "]"
    return h if h.count(":") > 1 else h.split(":")[0]


@app.middleware("http")
async def local_only(request: Request, call_next):
    if _host_of(request.headers.get("host")) not in LOCAL_HOSTS:
        return Response("只接受本机访问（Host 不对）", status_code=403, media_type="text/plain; charset=utf-8")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlsplit
            if (urlsplit(origin).hostname or "") not in LOCAL_HOSTS | {"::1"}:
                return Response("不接受别的网站发来的请求", status_code=403, media_type="text/plain; charset=utf-8")
        if request.headers.get("sec-fetch-site") == "cross-site":
            return Response("不接受别的网站发来的请求", status_code=403, media_type="text/plain; charset=utf-8")
    return await call_next(request)
_projects = {}
_recent = []                                                 # 最近用过的项目（新的在前）
KEEP = {"n": 2 if int(read_settings().get("keep_projects", 1)) == 2 else 1}
PROJ.ACTION_LOG["on"] = bool(read_settings().get("action_log", True))   # 操作记录（默认开）
ENG.CLEAN_UPLOADS["on"] = bool(read_settings().get("clean_uploads", True))   # 任务结束后删除上传到 ComfyUI 的素材（默认开）
PROJ.WORK_LONG["v"] = int(read_settings().get("work_long", PROJ.WORK_LONG["v"]))   # 导入视频的工作分辨率（长边，0 = 原尺寸）   # 设置「后台项目标签」：1 = 切走就放掉遮罩缓存，2 = 最近 2 个常驻
if read_settings().get("seq_preview") in SEQ.PREVIEW_CHOICES:   # 分段页整条预览的长边
    SEQ.PREVIEW["long"] = read_settings()["seq_preview"]
JOB = jobs.JOB
if hasattr(BASE, "report"):
    BASE.report = lambda info: JOB.update(progress=info)      # ComfyUI 的进度（阶段、第几步、剩余时间）→ 界面进度条
def proj(pid) -> Project:
    if not _recent or _recent[0] != pid:
        if pid in _recent:
            _recent.remove(pid)
        _recent.insert(0, pid)
        keep = set(_recent[:KEEP["n"]])
        for k, other in _projects.items():                 # 换了项目：最近 KEEP 个以外的遮罩缓存放掉（项目对象留着，后台任务手里的引用照常有效）
            if k not in keep and other._mask_cache:
                other._mask_cache.clear()
    if pid not in _projects:
        root = PROJECTS / pid
        if not (root / "project.json").exists():
            raise HTTPException(404, "项目不存在")
        _projects[pid] = Project(root)
    elif _projects[pid].changed_on_disk():                  # 被别的进程改过：重新读（遮罩缓存保留）
        _projects[pid].reload()
    return _projects[pid]


IM_NAMES = {"first": "首帧", "last": "尾帧", "bg": "背景图", "card": "资料卡", "key": "关键帧"}


def start_job(fn, name=None, pid=None):
    """后台任务；有任务在跑就排队（返回 queued）。name / pid 给界面任务列表显示。"""
    return jobs.start(fn, name, pid)


# ---------- 项目 ----------

API_VERSION = 259        # 前端会检查：服务端比页面旧时提示重启


@app.get("/api/info")
def info():
    return {"engine": ENGINE.name, "api": API_VERSION}


@app.get("/api/qwen")
def qwen_models():
    if not isinstance(BASE, ComfyEngine):
        return {"current": None, "models": []}
    have = BASE.qwen_available()
    models = [dict(key=k, label=m["label"], available=k in have) for k, m in BASE.QWEN_MODELS.items()]
    for m in models:                                          # 灰掉的写上缺哪个文件（悬停看）
        if not m["available"]:
            miss = BASE.model_missing(m["key"])
            if miss:
                m["note"] = "ComfyUI 上缺：" + "、".join(miss)
    for name in WF.names():                                   # 自己的改图工作流（workflows/image/*.json）；不合格的灰掉并写原因
        try:
            WF.load(name)
            models.append(dict(key=WF.PREFIX + name, label=f"工作流 · {name}", available=True))
        except ValueError as e:
            models.append(dict(key=WF.PREFIX + name, label=f"工作流 · {name}", available=False, note=str(e)))
    pe_have = BASE.pe_available()
    pes = [dict(key=k, label=m["label"], available=k in pe_have) for k, m in BASE.PE_MODELS.items()]
    return {"current": WF.PREFIX + BASE.image_workflow if BASE.image_workflow else BASE.qwen_model, "models": models,
            "pe": BASE.pe_model, "pes": pes}


@app.get("/api/model_files")
def get_model_files(group: str = "image"):
    """「模型文件」页（图片生成 / 视频生成面板 ComfyUI 按钮右边）：这一组每个模型文件现在用哪个、可选哪些（ComfyUI 上的文件）。"""
    if group not in ComfyEngine.FILE_GROUPS:
        raise HTTPException(400, "没有这一组")
    eng = BASE if isinstance(BASE, ComfyEngine) else _graph_engine()
    sections = [dict(title=t, slots=[eng.slot_info(k) for k in keys]) for t, keys in ComfyEngine.FILE_GROUPS[group]]
    known = all(sl["known"] for sec in sections for sl in sec["slots"])
    return dict(sections=sections, error=None if known else (
        "假引擎：没有连 ComfyUI，列不出文件" if not isinstance(BASE, ComfyEngine) else "读不到 ComfyUI 的模型列表"))


@app.post("/api/model_files")
def set_model_file(body: dict = Body(...)):
    """body = {slot, file}：file 为空 = 用默认（默认不在时自动找同一模型的其他量化版）。"""
    slot, name = str(body.get("slot") or ""), str(body.get("file") or "").strip()
    if slot not in ComfyEngine.FILE_SLOTS:
        raise HTTPException(400, "没有这个模型槽位")
    s = read_settings()
    if slot == "h3.unet":                                   # 和「视频生成 → 模型」下拉同一个设置
        s["h3_model"] = name or ComfyEngine.FILE_SLOTS[slot]["default"]
        write_settings(s)
        BASE.unet = s["h3_model"]
        eng = BASE if isinstance(BASE, ComfyEngine) else _graph_engine()
        return eng.slot_info(slot)
    mf = _model_files(s)
    if name:
        mf[slot] = name
    else:
        mf.pop(slot, None)
    s["model_files"] = mf
    write_settings(s)
    if isinstance(BASE, ComfyEngine):
        BASE.files = dict(mf)
    eng = BASE if isinstance(BASE, ComfyEngine) else _graph_engine()
    return eng.slot_info(slot)


@app.get("/api/vl_models")
def vl_models():
    """视频生成「空区分析」可选的看图模型：ComfyUI 上的（没有文件 / 节点的灰掉）。"""
    have = BASE.vl_available() if isinstance(BASE, ComfyEngine) else list(ComfyEngine.VL_MODELS)
    return {"models": [dict(key=k, label=m["label"], available=k in have) for k, m in ComfyEngine.VL_MODELS.items()]}


@app.post("/api/qwen/pe/{key}")
def set_pe_model(key: str):
    """PE（提示词改写）用哪个模型：和改图模型分开选；换改图模型时会自动切到那个模型自己的 PE。"""
    if not isinstance(BASE, ComfyEngine) or key not in BASE.PE_MODELS:
        raise HTTPException(400, "没有这个 PE")
    BASE.pe_model = key
    s = read_settings()
    s["pe_model"] = key
    write_settings(s)
    return qwen_models()


@app.post("/api/qwen/{key}")
def set_qwen_model(key: str):
    if not isinstance(BASE, ComfyEngine):
        raise HTTPException(400, "没有这个模型")
    s = read_settings()
    if key.startswith(WF.PREFIX):                             # 自己的改图工作流
        name = key[len(WF.PREFIX):]
        try:
            WF.load(name)
        except ValueError as e:
            raise HTTPException(400, str(e))
        BASE.image_workflow = s["image_workflow"] = name
        write_settings(s)
        return qwen_models()
    if key not in BASE.QWEN_MODELS:
        raise HTTPException(400, "没有这个模型")
    BASE.qwen_model = key
    BASE.image_workflow = None
    s.pop("image_workflow", None)
    s["qwen_model"] = key
    BASE.pe_model = s["pe_model"] = BASE.QWEN_MODELS[key].get("pe", "qwen")   # 默认跟模型：换模型 = 换成它自己的 PE
    write_settings(s)
    return qwen_models()


# ---------- 云端 API（图片生成 / 视频生成面板切到 API 时用；平台由用户按接口格式自己配）----------

def providers_state():
    c = ENGINE.cfg
    k = ENGINE.api.key()
    profiles = [dict(id=i, name=pf.get("name", i), format=pf["format"], profile=pf) for i, pf in APIE.CUSTOM.items()]
    return dict(c, key_set=bool(k), key_tail=k[-4:] if len(k) >= 12 else "", comfy=isinstance(BASE, ComfyEngine),
                profiles=profiles, profile=APIE.profile(c["provider"]), templates=APIE.TEMPLATES)


@app.get("/api/providers")
def get_providers():
    return providers_state()


@app.post("/api/providers")
def set_providers(body: dict = Body(...)):
    """只收已知字段。key：这个平台的 API key（填了才改；"" 不改；clear_key = 删掉），存 api_keys.local.json，不进 app_settings.json。"""
    c = dict(ENGINE.cfg)
    for k in API_DEFAULTS:
        if k in body and k != "key":
            c[k] = str(body[k]).strip() or API_DEFAULTS[k]
    if c["provider"] and c["provider"] not in APIE.CUSTOM:
        raise HTTPException(400, f"没有这个平台：{c['provider']}")
    if not c["provider"] and (body.get("clear_key") or str(body.get("key") or "").strip()):
        raise HTTPException(400, "先新建一个平台，再填 key")
    try:
        if body.get("clear_key"):
            save_api_key(c["provider"], "")
        elif str(body.get("key") or "").strip():
            save_api_key(c["provider"], body["key"])
    except RuntimeError as e:                                 # api_keys.local.json 坏了：不覆盖，告诉用户
        raise HTTPException(400, str(e))
    _save_api(c)
    return providers_state()


@app.post("/api/providers/profile")
def save_provider_profile(body: dict = Body(...)):
    """存一个平台配置（{id?, name, profile}）：按接口格式填地址、字段位置；id 不给 = 新建。存 app_settings.json 的 api.profiles。"""
    pf = dict(body.get("profile") or {})
    pf["name"] = str(body.get("name") or pf.get("name") or "").strip()[:40]
    if not pf["name"]:
        raise HTTPException(400, "先给这个平台起个名字")
    err = APIE.check_profile(pf)
    if err:
        raise HTTPException(400, err)
    pid = str(body.get("id") or "").strip() or "custom-" + uuid.uuid4().hex[:6]
    APIE.CUSTOM[pid] = pf
    _save_api({**ENGINE.cfg, "provider": pid, "base_url": pf["base_url"], "image_model": pf["image_model"]})
    return providers_state()


@app.delete("/api/providers/profile/{pid}")
def delete_provider_profile(pid: str):
    if pid not in APIE.CUSTOM:
        raise HTTPException(400, "没有这个平台")
    try:
        save_api_key(pid, "")
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    APIE.CUSTOM.pop(pid)
    c = dict(ENGINE.cfg)
    if c["provider"] == pid:                                   # 删的是正在用的：换成剩下的第一个（没有 = 不设平台）
        nxt = next(iter(APIE.CUSTOM), "")
        pf = APIE.CUSTOM.get(nxt) or {}
        c.update(provider=nxt, base_url=pf.get("base_url", ""), image_model=pf.get("image_model", ""))
    _save_api(c)
    return providers_state()


@app.get("/api/providers/models")
def provider_models():
    """API 模式的模型下拉：平台上的图片模型 + 现在填的那个（平台不给列表时只有它，可以手填）。"""
    have = []
    try:
        have = ENGINE.api.image_models()
    except Exception:
        pass
    cur = ENGINE.cfg["image_model"]
    return {"current": cur, "models": sorted((set(have) | {cur}) - {""}), "listed": bool(have)}


@app.post("/api/providers/check")
def check_providers():
    try:
        return ENGINE.api.check()
    except Exception as e:
        raise HTTPException(400, str(e))


# ---------- 图片标签：首帧 / 尾帧 / 背景图 / 资料卡 ----------

@app.post("/api/p/{pid}/images")
def image_create(pid: str, body: dict = Body(...)):
    """新建图片标签（已有同类的就直接返回它）。取原图要合成一帧，放到后台任务里做。"""
    p = proj(pid)
    kind, oid = body.get("kind"), body.get("oid")
    if kind == "key":                                    # 关键帧：可以多张，frame = 视频编辑播放头停的那一帧
        try:
            frame = int(body.get("frame"))
        except (TypeError, ValueError):
            raise HTTPException(400, "关键帧要给帧号")
        if not 0 <= frame < p.data["n"]:
            raise HTTPException(400, f"帧号 {frame} 超出范围（0–{p.data['n'] - 1}）")
        start_job(lambda log: RE.create(p, kind, None, log, frame=frame), "关键帧准备", pid)
        return {"pending": True}
    old = IM.find(p, kind, oid)
    if old or kind == "bg":
        return RE.create(p, kind, oid)
    if kind == "card" and not oid:
        raise HTTPException(400, "资料卡要指定一个图层")
    start_job(lambda log: RE.create(p, kind, oid, log), IM_NAMES.get(kind, "图片") + "准备", pid)
    return {"pending": True}


@app.post("/api/p/{pid}/images/{iid}/frame")
def image_key_frame(pid: str, iid: str, body: dict = Body(...)):
    """关键帧改帧号：按新的那一帧重新取画面（旧候选从列表去掉，文件留着）。"""
    p = proj(pid)
    it = IM.get(p, iid)
    if it["kind"] != "key":
        raise HTTPException(400, "只有关键帧能改帧号")
    try:
        frame = int(body.get("frame"))
    except (TypeError, ValueError):
        raise HTTPException(400, "帧号要是整数")
    if not 0 <= frame < p.data["n"]:
        raise HTTPException(400, f"帧号 {frame} 超出范围（0–{p.data['n'] - 1}）")
    return start_job(lambda log: RE.set_key_frame(p, it, frame, log), "关键帧提取画面", pid)


@app.post("/api/p/{pid}/images/{iid}/base")
def image_rebase(pid: str, iid: str):
    """按现在的图层摆放重新取原图（旧候选和挑选清空，文件留在磁盘上）。"""
    p = proj(pid)
    it = IM.get(p, iid)
    return start_job(lambda log: RE.take_base(p, it, log), IM_NAMES.get(it.get("kind"), "图片") + "提取画面", pid)


@app.post("/api/p/{pid}/images/{iid}/cands")
def image_cands(pid: str, iid: str, body: dict = Body(...)):
    p = proj(pid)
    it = IM.get(p, iid)
    desc = str(body.get("desc", "")).strip()                      # 用户描述 + 系统基础提示（两段，拼起来发给 Qwen）
    sys = str(body.get("sys", body.get("prompt", ""))).strip()
    if not desc and not sys:
        raise HTTPException(400, "先写提示词")
    count = int(np.clip(int(body.get("count", IM.N_CANDS)), 1, 8))
    use_bg = body.get("use_bg")
    fill = body.get("fill")
    opts = {k: body[k] for k in RE.IMG_MASK_DEFAULTS if body.get(k) is not None}
    pe, body_, parts = body.get("pe"), body.get("body"), body.get("body_parts")
    seed = body.get("seed")
    seed = None if seed in (None, "") else int(seed)
    mode = body.get("mode") if body.get("mode") in ("inpaint", "gen") else None
    return start_job(lambda log: RG.candidates(p, ENGINE, iid, desc, sys, count, log, None if use_bg is None else bool(use_bg),
                                               fill if fill in RP.FILLS else None, opts,
                                               None if pe is None else bool(pe), None if body_ is None else bool(body_), parts, seed, mode,
                                               MN.parse(body.get("mentions"))),
                     IM_NAMES.get(it.get("kind"), "图片") + "生成", pid)


@app.post("/api/p/{pid}/images/{iid}/api_cands")
def image_api_cands(pid: str, iid: str, body: dict = Body(...)):
    """API 模式出候选：只发输入图 + 提示词（原样）给设置里的云端平台；和 ComfyUI 那条路（/cands）分开。"""
    p = proj(pid)
    it = IM.get(p, iid)
    prompt = str(body.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(400, "先写提示词")
    _need_api()
    count = int(np.clip(int(body.get("count", IM.N_CANDS)), 1, 8))
    mentions = MN.parse(body.get("mentions"))
    return start_job(lambda log: RG.api_candidates(p, ENGINE.api, iid, prompt, count, log, mentions),
                     IM_NAMES.get(it.get("kind"), "图片") + "生成（API）", pid)


# ---------- 导出：模型的输入整理成 zip，拿去自己的 ComfyUI 跑（exporter.py） ----------

def _graph_engine():
    """搭 ComfyUI 工作流用的引擎（只搭图、不联网）：连着 4090 就是它；假引擎时按设置里的模型 / LoRA 新建一个。"""
    if isinstance(BASE, ComfyEngine):
        return BASE
    g = ComfyEngine("http://127.0.0.1:8188")
    st = read_settings()
    g.loras, g.unet = st.get("h3_loras", []), st.get("h3_model") or g.unet
    g.files, g.offline = _model_files(st), True                # 模型文件照设置；不查 ComfyUI 的列表
    if st.get("qwen_model") in g.QWEN_MODELS:                 # 改图模型也照设置（Qwen / MiniMax H3 Image 的工作流不一样）
        g.qwen_model = st["qwen_model"]
    return g


def _export_rel(p, f):
    return str(Path(f).relative_to(p.root / EXP.EXPORT_DIR))


@app.post("/api/p/{pid}/export/video")
def export_video(pid: str, op: dict = Body(...)):
    """视频编辑：按现在的编辑准备一次生成的全部素材（粗剪、遮罩、<Video 1>、参考图、提示词、工作流），不调 H3。
    提示词要问 4090 的 Qwen-VL，所以走后台任务（排队），完成后前端按 export_file 下载。"""
    p = proj(pid)
    return start_job(lambda log: {"export_file": _export_rel(p, EXP.export_video(p, ENGINE, _graph_engine(), op, log))},
                     "导出视频生成素材", pid)


@app.post("/api/p/{pid}/export/image/{iid}")
def export_image(pid: str, iid: str, body: dict = Body(...)):
    """图片编辑：这一帧（空处涂灰）+ 遮罩 + 参考图（Picture N 顺序）+ 提示词（前端给现在面板上的）+ Qwen 工作流。"""
    p = proj(pid)
    it = IM.get(p, iid)
    if not (IM.folder(p, iid) / "base.png").exists():
        raise HTTPException(400, "这张图还没有画面：先在图片编辑里出一次候选或刷新原图")
    d = IM.folder(p, iid)
    refs, keys = [], ["base"]
    if it["kind"] in IM.STILLS and it.get("use_bg", True) and not it.get("base_upload"):
        bg = PANO.to_frame(p, it["frame"], d, it.get("bg_choice"))
        if bg:
            refs.append(("background", bg))
            keys.append("bg_local" if it.get("bg_choice") == "local" else "bg")
    for r in it.get("refs", []):
        if (d / f"ref_{r['id']}.png").exists():
            refs.append(((r.get("label") or "ref"), d / f"ref_{r['id']}.png"))
            keys.append("ref_" + r["id"])
    ge = _graph_engine()
    prompt = MN.resolve(str(body.get("prompt") or ""), MN.parse(body.get("mentions")), keys,
                        MN.PICTURE_QWEN if body.get("api") else MN.picture_format(ge))
    f = EXP.export_image(p, it, ge, prompt, body.get("mode"), refs)
    return {"file": _export_rel(p, f)}


@app.post("/api/p/{pid}/export/result/{rid}")
def export_result(pid: str, rid: str):
    """视频结果：成片 + 当时交给 H3 的素材（从那次生成的文件夹里取）。"""
    p = proj(pid)
    f, missing = EXP.export_result(p, _result(p, rid))
    return {"file": _export_rel(p, f), "missing": missing}


@app.post("/api/p/{pid}/export/cands/{iid}")
def export_cands(pid: str, iid: str, body: dict = Body(default={})):
    """图片结果：names = 要下载的候选（不给 = 选用的那张，没选用就最新一张）；all = 全部候选。"""
    p = proj(pid)
    it = IM.get(p, iid)
    names = list(it.get("cands", [])) if body.get("all") else [n for n in (body.get("names") or []) if n in it.get("cands", [])]
    if not names:
        names = [it["pick"]] if it.get("pick") else it.get("cands", [])[-1:]
    if not names:
        raise HTTPException(400, "还没有候选图")
    return {"file": _export_rel(p, EXP.export_cands(p, it, names))}


@app.get("/api/p/{pid}/export/file/cand/{iid}")
def export_cand_file(pid: str, iid: str, name: str = ""):
    """图片结果：直接下载一张候选图（不打包）。name 不给 = 选用的那张，没选用就最新一张。
    文件名和结果文件夹里的一样：项目名_vN_首帧_K.png（背景图：项目名_背景图_K.png）。"""
    p = proj(pid)
    it = IM.get(p, iid)
    if it.get("kind") == "bg":                             # 背景图的候选在全景图文件夹里（panorama.candidates + 整张 / 只补洞 / 上传的），选用的 = use
        pn = p.data.get("panorama") or {}
        d = PANO.folder(p)
        cands = [n for n in [*pn.get("candidates", []), "qwen_full", "filled", "upload"] if (d / f"{n}.png").is_file()]
        name = name if name in cands else (pn.get("use") if pn.get("use") in cands else (cands[-1] if cands else ""))
        if not name:
            raise HTTPException(400, "还没有背景图")
        return FileResponse(d / f"{name}.png", media_type="image/png", filename=IM.cand_file_name(p, it, name))
    cands = it.get("cands", [])
    name = name if name in cands else (it.get("pick") or (cands[-1] if cands else ""))
    if not name:
        raise HTTPException(400, "还没有候选图")
    try:
        f = IM.file_path(p, iid, name)
    except FileNotFoundError:
        raise HTTPException(404, "没有这张图")
    return FileResponse(f, media_type="image/png", filename=IM.cand_file_name(p, it, name))


@app.get("/api/p/{pid}/export/file/result/{rid}")
def export_result_file(pid: str, rid: str):
    """视频结果：直接下载这条成片（不打包），文件名就是它在项目里的文件名（项目名_vN_K.mp4）。
    地址不带 .mp4、类型 octet-stream：浏览器当下载处理，不在页面里播放。"""
    p = proj(pid)
    f = p.root / _result(p, rid)["file"]
    if not f.is_file():
        raise HTTPException(404, "这条结果的视频文件不在了")
    return FileResponse(f, media_type="application/octet-stream", filename=f.name)


@app.get("/api/p/{pid}/export/download")
def export_download(pid: str, file: str):
    """下载导出的 zip（只给 导出export/ 里的 .zip；类型 application/zip，不碰 .mp4 地址，IDM 不会误拦播放）。"""
    p = proj(pid)
    d = (p.root / EXP.EXPORT_DIR).resolve()
    f = (d / file).resolve()
    if not f.is_relative_to(d) or f.suffix != ".zip" or not f.is_file():
        raise HTTPException(404, "没有这个导出文件")
    return FileResponse(f, media_type="application/zip", filename=f.name)


def _reveal(path: Path):
    """在系统文件管理器里显示这个文件：Windows / macOS 选中它，Linux 打开所在文件夹。
    没有文件管理器（无桌面的服务器）就只打日志，接口照常返回路径。"""
    if sys.platform == "win32":
        cmd = ["explorer.exe", "/select,", str(path)]
    elif sys.platform == "darwin":
        cmd = ["open", "-R", str(path)]
    else:
        cmd = ["xdg-open", str(path.parent)]
    try:
        subprocess.Popen(cmd)
    except OSError as e:
        print(f"[reveal] 打不开文件管理器：{e}", flush=True)


@app.post("/api/p/{pid}/export/reveal")
def export_reveal(pid: str, body: dict = Body(...)):
    p = proj(pid)
    d = (p.root / EXP.EXPORT_DIR).resolve()
    f = (d / str(body.get("file") or "")).resolve()
    if not f.is_relative_to(d) or not f.is_file():
        raise HTTPException(404, "没有这个导出文件")
    _reveal(f)
    return {"path": str(f)}


@app.post("/api/p/{pid}/images/{iid}/describe")
def image_describe(pid: str, iid: str):
    """Qwen-VL 看这个标签的原图写描述（填进用户描述那一段）。"""
    p = proj(pid)
    it = IM.get(p, iid)
    if not hasattr(ENGINE, "describe"):
        raise HTTPException(400, "现在的引擎不能看图写描述")
    return start_job(lambda log: RP.describe(p, ENGINE, iid, log), IM_NAMES.get(it.get("kind"), "图片") + "描述", pid)


@app.get("/api/image_sys_default")
def image_sys_default():
    return {"still": RP.STILL_PROMPT, "gen": RP.GEN_PROMPT, "upload": RP.UPLOAD_PROMPT, "old": [RP._OLD_STILL]}   # old：没改过的旧默认，前端换成新的


@app.post("/api/p/{pid}/images/{iid}/cands_upload")
async def image_cand_upload(pid: str, iid: str, request: Request):
    """上传一张图直接当候选（请求体 = 图片文件）。"""
    data = await request.body()
    if not data:
        raise HTTPException(400, "没有收到图片")
    try:
        return IM.add_cand_upload(proj(pid), iid, data)
    except (OSError, KeyError) as e:
        raise HTTPException(400, f"上传失败：{e}")


@app.post("/api/p/{pid}/images/{iid}/base_upload")
async def image_base_upload(pid: str, iid: str, request: Request):
    """图片编辑里上传一张要调整的图，代替从视频取的画面（请求体 = 图片文件）。"""
    data = await request.body()
    if not data:
        raise HTTPException(400, "没有收到图片")
    p = proj(pid)
    try:
        return RE.upload_base(p, IM.get(p, iid), data)
    except (OSError, KeyError) as e:
        raise HTTPException(400, f"上传失败：{e}")


@app.post("/api/p/{pid}/images/{iid}/base_from_cand")
def image_base_from_cand(pid: str, iid: str, body: dict = Body(...)):
    """图片结果「用这张继续编辑」：把这张候选当成这个标签的画面（和上传一张图一样），之后在图片编辑里接着出候选。"""
    p = proj(pid)
    it = IM.get(p, iid)
    name = body.get("name")
    if name not in it.get("cands", []):
        raise HTTPException(404, "没有这张候选图")
    path = p.image_dir(it) / f"{name}.png"
    if not path.is_file():
        raise HTTPException(404, "候选图文件不在了")
    it = RE.upload_base(p, it, path.read_bytes())
    it["base_from"] = name
    p.save()
    return it


@app.post("/api/p/{pid}/images/{iid}/repaint")
async def image_repaint(pid: str, iid: str, request: Request):
    """局部重绘范围：请求体 = 蒙版图（白 = 重绘）；空的 = 清除。之后出候选只重绘这块。"""
    p = proj(pid)
    it = IM.get(p, iid)
    try:
        RE.set_repaint(p, it, await request.body())
    except ValueError as e:
        raise HTTPException(400, str(e))
    p.save()
    return it


@app.delete("/api/p/{pid}/images/{iid}/repaint")
def image_repaint_clear(pid: str, iid: str):
    p = proj(pid)
    it = IM.get(p, iid)
    RE.drop_repaint(p, it)
    p.save()
    return it


@app.get("/api/p/{pid}/images/{iid}/repaint.png")
def image_repaint_png(pid: str, iid: str, overlay: int = 0):
    """局部重绘范围：overlay=1 = 半透明青色（叠在图上看），否则白 = 范围、透明 = 其余（画的时候接着改）。"""
    p = proj(pid)
    it = IM.get(p, iid)
    m = RE.load_repaint(p, it)
    if m is None:
        raise HTTPException(404, "没有局部重绘范围")
    a = m.astype(np.uint8) * (110 if overlay else 255)
    rgb = (24, 200, 232) if overlay else (255, 255, 255)
    img = np.dstack([np.where(m, c, 0).astype(np.uint8) for c in rgb] + [a])
    buf = io.BytesIO()
    Image.fromarray(img, "RGBA").save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/p/{pid}/images/{iid}/base_reset")
def image_base_reset(pid: str, iid: str):
    """改回从视频取的画面（候选保留）。用候选图接着编辑的标签不能改回（2026-11-11 用户定：那张图就是新的起点）。"""
    p = proj(pid)
    it = IM.get(p, iid)
    if it.get("base_from"):
        raise HTTPException(400, "这个标签在用候选图接着编辑，不能改回视频画面")
    return start_job(lambda log: RE.take_base(p, it, log, keep=True), IM_NAMES.get(it.get("kind"), "图片") + "提取画面", pid)


@app.post("/api/p/{pid}/panorama/plate_upload")
async def panorama_plate_upload(pid: str, request: Request):
    data = await request.body()
    try:
        return PANO.upload_plate(proj(pid), data)
    except (OSError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/p/{pid}/panorama/result_upload")
async def panorama_result_upload(pid: str, request: Request):
    data = await request.body()
    try:
        return PANO.upload_result(proj(pid), data)
    except (OSError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/p/{pid}/images/{iid}/pick")
def image_pick(pid: str, iid: str, body: dict = Body(...)):
    p = proj(pid)
    try:
        return IM.pick(p, iid, body.get("name"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/p/{pid}/images/{iid}/refs")
async def image_add_ref(pid: str, iid: str, request: Request, src: str = "", from_iid: str = "", name: str = "", i: int = -1, replace: str = ""):
    """添加额外参考图。src=upload：请求体是图片文件；src=tab：别的图片标签 from_iid 里的 name；src=frame：资料卡从第 i 帧抠物体。"""
    p = proj(pid)
    try:
        if src == "upload":
            data = await request.body()
            if not data:
                raise ValueError("没有收到图片")
            return IM.add_ref_upload(p, iid, data, replace)
        if src == "tab":
            return IM.add_ref_tab(p, iid, from_iid, name)
        if src == "frame":
            return IM.add_ref_frame(p, iid, i)
    except (ValueError, KeyError, FileNotFoundError, OSError) as e:
        raise HTTPException(400, f"添加参考图失败：{e}")
    raise HTTPException(400, "src 只能是 upload / tab / frame")


@app.post("/api/p/{pid}/images/{iid}/refs/{rid}")
def image_ref_note(pid: str, iid: str, rid: str, body: dict = Body(...)):
    return IM.set_ref_note(proj(pid), iid, rid, body.get("note", ""))


@app.delete("/api/p/{pid}/images/{iid}/refs/{rid}")
def image_ref_delete(pid: str, iid: str, rid: str):
    return IM.remove_ref(proj(pid), iid, rid)


@app.delete("/api/p/{pid}/images/{iid}")
def image_delete(pid: str, iid: str):
    p = proj(pid)
    IM.remove(p, iid)
    return p.data


# ---------- 其他图片（只在视频生成栏里当参考图） ----------

@app.post("/api/p/{pid}/gen_refs")
async def gen_ref_add(pid: str, request: Request):
    """上传一张「其他图片」：请求体 = 图片文件。"""
    data = await request.body()
    if not data:
        raise HTTPException(400, "没有收到图片")
    try:
        return IM.add_gen_ref(proj(pid), data)
    except (OSError, ValueError) as e:
        raise HTTPException(400, f"上传失败：{e}")


@app.delete("/api/p/{pid}/gen_refs/{rid}")
def gen_ref_delete(pid: str, rid: str):
    return IM.remove_gen_ref(proj(pid), rid)


@app.get("/api/p/{pid}/gen_refs/{rid}.png")
def gen_ref_file(pid: str, rid: str, w: int = 0):
    p = proj(pid)
    f = IM.gen_ref_path(p, rid)
    if not rid.replace("_", "").isalnum() or not f.exists():
        raise HTTPException(404, "还没有这张图")
    if not w:
        return FileResponse(f, media_type="image/png", headers={"Cache-Control": "no-cache"})
    im = Image.open(f)
    im = im.resize((w, max(1, round(im.height * w / im.width))), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=90)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@app.post("/api/p/{pid}/images/{iid}/layer_opts")
def image_layer_opts(pid: str, iid: str, body: dict = Body(...)):
    """图片标签里改「蒙版与重绘区域」：{oids, fields}，只存进这张图（默认继承视频的值，不影响视频）。点「取现在的画面」生效。"""
    p = proj(pid)
    try:
        return RE.set_layer_opts(p, iid, body.get("oids") or [], body.get("fields") or {})
    except KeyError as e:
        raise HTTPException(404, f"没有这个图层：{e}")


@app.get("/api/p/{pid}/images/{iid}/{name}.png")
def image_file(pid: str, iid: str, name: str, w: int = 0, v: str = ""):
    try:
        f = IM.file_path(proj(pid), iid, name)
    except FileNotFoundError:
        raise HTTPException(404, "没有这张图")
    if not w:
        return FileResponse(f, headers={"Cache-Control": "max-age=3600"})
    im = Image.open(f)
    im.thumbnail((w, w))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "max-age=3600"})


_zone_lock = threading.Lock()


@app.get("/api/p/{pid}/zones/{i}.png")
def zone_map(pid: str, i: int, g: str = "", mode: str = "pixels"):
    """界面“重绘范围”叠加层：第 i 帧的分区（预览分辨率）。红通道 = 区号 × 60（0 保留区，1 重绘，2 空区，3 本体，4 过渡）。
    g 只用来让浏览器换缓存；按服务端当前保存的图层状态算。"""
    p = proj(pid)
    if not 0 <= i < p.data["n"]:
        raise HTTPException(404, "没有这一帧")
    sig = IM.edit_sig(p) + str(p.data.get("trans"))   # 过渡宽度只影响视频分区，不进 edit_sig（不让参考图过期）
    with _zone_lock:
        z = cell_overlay_map(p, i, sig, _zone_lock) if mode == "cells" else zone_overlay_map(p, i, sig, _zone_lock)
    if z is None:
        return Response(status_code=503, headers={"Retry-After": "1", "Cache-Control": "no-store"})
    buf = io.BytesIO()
    Image.fromarray((z * 60).astype(np.uint8)).save(buf, "PNG")
    # 不让浏览器缓存：前端的 g（S.zoneGen）每次打开页面都从 0 开始，同一个地址会拿到改之前的旧分区图（主体扩绘区改了画面不变）。
    # 服务端按图层签名 sig 有内存缓存，重新请求很便宜；前端自己也把解好的分区图留在内存里。
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


def pass_info(path):
    """视频结果的生成信息：从这次生成文件夹里的 inputused.txt 读出要点（模型、LoRA、步数、seed、参考图、帧数、提示词）。"""
    info = {"folder": str(path.parent)}
    f = path.parent / "inputused.txt"
    if not f.is_file():
        return info
    text = f.read_text(encoding="utf-8", errors="replace")
    sec, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^【(.+?)】", line)
        if m:
            cur = m.group(1); sec[cur] = []; continue
        if cur:
            sec[cur].append(line)
        elif line.startswith("时间："):
            info["time"] = line[3:].strip()
        elif line.startswith("生成："):
            info["kind"] = line[3:].strip()
    model = sec.get("模型", [])
    info["model"] = next((l.split("：", 1)[1].strip() for l in model if "H3 主模型" in l), "")
    info["loras"] = [l.split("：", 1)[1].strip() for l in model if l.strip().startswith("LoRA")]
    samp = " ".join(sec.get("采样", []))
    m = re.search(r"(\d+) 步", samp); info["steps"] = int(m.group(1)) if m else None
    m = re.search(r"seed (\d+)", samp); info["seed"] = int(m.group(1)) if m else None
    m = re.search(r"采样器 (\S+?)，", samp); info["sampler"] = m.group(1) if m else ""
    info["refs"] = [m.group(1) for l in next((v for k, v in sec.items() if k.startswith("参考图")), [])
                    for m in [re.search(r"<Picture \d+>（(.+?)）", l)] if m]
    other = " ".join(sec.get("其他", []))
    m = re.search(r"原片 (\d+) 帧", other); info["frames"] = int(m.group(1)) if m else None
    info["prompt"] = "\n".join(sec.get("提示词", [])).strip()
    return info


@app.get("/api/p/{pid}/results/{rid}/info")
def result_info(pid: str, rid: str):
    """视频结果的生成信息（视频结果页右栏显示）。"""
    p = proj(pid)
    r = _result(p, rid)
    return {**{k: r.get(k) for k in ("label", "name", "time", "w", "h", "base")}, **pass_info(p.root / r["file"])}


def _result(p, rid):
    r = next((r for r in p.data.get("results", []) if r["id"] == rid), None)
    if r is None:
        raise HTTPException(404, "没有这个结果")
    return r


@app.post("/api/p/{pid}/reveal_output")
def reveal_output(pid: str, body: dict = Body(...)):
    p = proj(pid)
    kind, name = body.get("kind"), body.get("name")
    if kind == "video":
        path = p.root / _result(p, name)["file"]
    elif kind == "image":
        it = IM.get(p, body.get("iid"))
        if name not in it.get("cands", []):
            raise HTTPException(404, "没有这张候选图")
        path = p.image_dir(it) / (name + ".png")
    elif kind == "version":                              # 版本面板 ⋯「在资源管理器中显示」：这个版本的视频
        if not any(v["id"] == name for v in p.data["versions"]):
            raise HTTPException(404, "没有这个版本")
        path = p.video(name)
    elif kind == "background":
        pn = p.data.get("panorama") or {}
        if name not in [*pn.get("candidates", []), *pn.get("extracts", {}), "plate", "filled", "upload", "qwen_full"]:
            raise HTTPException(404, "没有这张背景图")
        path = p.panorama_dir() / (name + ".png")
    else:
        raise HTTPException(400, "未知结果类型")
    path = path.resolve()
    if not path.is_relative_to(p.root.resolve()) or not path.is_file():
        raise HTTPException(404, "结果文件不存在")
    _reveal(path)
    return {"path": str(path)}


@app.get("/api/p/{pid}/results/{rid}/video")
def result_video(pid: str, rid: str):
    """结果视频。不用 .mp4 / video/* 的地址和类型：下载工具（IDM）会拦截，前端按 blob 播放。"""
    p = proj(pid)
    f = p.root / _result(p, rid)["file"]
    if not f.exists():
        raise HTTPException(404, f"文件不在了：{f}")
    return FileResponse(f, media_type="application/octet-stream")


@app.get("/api/p/{pid}/results/{rid}/thumb")
def result_thumb(pid: str, rid: str):
    """结果列表的缩略图：视频中间那一帧，宽 160，缓存在项目缓存文件夹里。"""
    p = proj(pid)
    return _video_thumb(p.root / _result(p, rid)["file"], p.cache_dir() / "result_thumbs" / f"{rid}.jpg", 160)


def _video_thumb(f, out, w):
    """视频中间那一帧截成 jpg（宽 w），缓存在 out；视频比缓存新就重截。"""
    if not out.exists() or out.stat().st_mtime < f.stat().st_mtime:
        out.parent.mkdir(parents=True, exist_ok=True)
        dur = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                             capture_output=True).stdout.decode("utf-8", "replace").strip()
        try:
            t = float(dur) / 2
        except ValueError:
            t = 0
        subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(f), "-frames:v", "1",
                        "-vf", f"scale={w}:-2", "-q:v", "4", str(out)], capture_output=True)
        if not out.exists():
            raise HTTPException(404, "截不到缩略图")
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


# ---------- 所有结果（顶栏「所有结果」，跨项目）----------
# 只读，而且不经过 proj()：proj() 会把这个项目记成「最近用的」，放掉当前项目的遮罩缓存。
_peeked = {}


def _peek(pid):
    if not pid or any(c in pid for c in "/\\") or pid in (".", ".."):
        raise HTTPException(404, "项目不存在")
    if pid in _projects:
        return proj_loaded(pid)
    root = PROJECTS / pid
    if not (root / "project.json").exists():
        raise HTTPException(404, "项目不存在")
    p = _peeked.get(pid)
    if p is None:
        p = _peeked[pid] = Project(root)
    elif p.changed_on_disk():
        p.reload()
    return p


def proj_loaded(pid):
    p = _projects[pid]
    if p.changed_on_disk():
        p.reload()
    return p


@app.get("/api/gallery")
def gallery_list(all: int = 0):
    out = []
    for d in PROJECTS.iterdir():
        if (d / "project.json").exists():
            try:
                out += GAL.items(_peek(d.name), bool(all))
            except Exception as e:                       # 一个项目坏了不影响别的
                print(f"所有结果：跳过 {d.name}：{e}")
    out.sort(key=lambda x: x["t"], reverse=True)
    return out


def _gallery_file(pid, type, id, cand):
    p = _peek(pid)
    try:
        f = GAL.file_of(p, type, id, cand)
    except KeyError:
        raise HTTPException(404, "没有这个结果")
    if not f.exists():
        raise HTTPException(404, "文件不在了")
    return p, f


@app.get("/api/gallery/thumb")
def gallery_thumb(pid: str, type: str, id: str, cand: str = ""):
    p, f = _gallery_file(pid, type, id, cand)
    key = re.sub(r"[^0-9A-Za-z_-]", "_", f"{type}_{id}_{cand}")
    out = p.cache_dir() / "gallery_thumbs" / f"{key}.jpg"
    if type == "video":
        return _video_thumb(f, out, 320)
    if not out.exists() or out.stat().st_mtime < f.stat().st_mtime:
        out.parent.mkdir(parents=True, exist_ok=True)
        im = Image.open(f).convert("RGB")
        im.thumbnail((320, 320))
        im.save(out, "JPEG", quality=85)
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/gallery/video")
def gallery_video(pid: str, id: str):
    """鼠标停在视频格子上时播放。和结果视频一样不用 .mp4 / video/*（IDM 会拦截），前端按 blob 播放。"""
    _, f = _gallery_file(pid, "video", id, "")
    return FileResponse(f, media_type="application/octet-stream")


# ---------- 长片段分段（app/sequence.py）----------
def _seq(sid):
    try:
        return SEQ.root_of(PROJECTS, sid)
    except KeyError:
        raise HTTPException(404, "长片段不存在")


def _seq_view(sid):
    """分段页要的数据：每段带上它的项目名、视频结果列表、导出时会用哪个。"""
    root = _seq(sid)
    d = SEQ.load(root)
    for s in d["segments"]:
        rs = SEQ.seg_results(PROJECTS, s)
        s["results"] = [{k: r[k] for k in ("id", "label", "time")} for r in rs]
        s["pick"] = SEQ.pick(PROJECTS, s)[1]
        if s.get("pid") and not (PROJECTS / s["pid"] / "project.json").is_file():
            s["pid"] = None                                  # 项目被删了：当没建过
    d["ask_s"], d["max_s"] = SEQ.ASK_S, SEQ.SEG_MAX_S
    key = SEQ.mix_key(PROJECTS, d)
    d["mix_ready"] = SEQ.preview_file(PROJECTS, root, d).name == "preview_mix.mp4"
    d["preview_old"] = SEQ.preview_old(root, d)              # 设置里换了分段预览清晰度：要重做预览
    d["mix_stale"] = key != (d.get("mix_built") or "") or d["preview_old"]   # 锁定变了、整条预览还没重拼
    d["preview_v"] = format(zlib.crc32(f"{SEQ.preview_long(root, d)}:{key if d['mix_ready'] else ''}".encode()), "x")
    return d


def _seq_mix(sid):
    """锁定变了：后台重拼分段页的整条预览（返回任务）；没变就直接返回分段数据。"""
    d = _seq_view(sid)
    if d["mix_stale"]:
        return start_job(lambda log: SEQ.build_mix(PROJECTS, sid, log), "拼整条预览")
    return d


@app.post("/api/probe_video")
def probe_video(body: dict = Body(...)):
    """打开视频前先看多长：超过 ask_s 秒前端问要不要分段（不读帧，长视频也快）。"""
    path = str(body.get("path", "")).strip().strip('"')
    if not Path(path).is_file():
        raise HTTPException(400, f"找不到视频：{path}")
    try:
        info = SEQ.vprobe(path)
    except Exception as e:
        raise HTTPException(400, f"读不了这个视频：{e}")
    return {**{k: info[k] for k in ("w", "h", "fps", "n", "duration")}, "ask_s": SEQ.ASK_S}


@app.get("/api/sequences")
def list_sequences():
    return SEQ.list_all(PROJECTS)


@app.post("/api/sequences")
def create_sequence(body: dict = Body(...)):
    path = str(body.get("video_path", "")).strip().strip('"')
    if not Path(path).is_file():
        raise HTTPException(400, f"找不到视频：{path}")
    return start_job(lambda log: SEQ.create(PROJECTS, path, log), "长片段导入")


@app.get("/api/s/{sid}")
def get_sequence(sid: str):
    return _seq_view(sid)


@app.post("/api/s/{sid}/mix")
def sequence_mix(sid: str):
    """整条预览没拼好（上次拼失败、或是旧版本留下的）：打开分段页时补拼一次。"""
    return _seq_mix(sid)


@app.post("/api/s/{sid}/starts")
def sequence_starts(sid: str, body: dict = Body(...)):
    """改分段：body.starts = 每段的起点帧号（不含 0）。已经在编辑的段切点不能动，
    除非它的 id 在 body.reset 里（用户确认过）：那一段的项目先挪进回收站、当没建过，之后按新范围重新剪。"""
    root = _seq(sid)
    d = SEQ.load(root)
    starts = [int(x) for x in body.get("starts", [])]
    reset = set(body.get("reset") or [])
    keys = {tuple(p) for p in SEQ.pieces(starts, d["n"])}
    gone = [s for s in d["segments"] if s.get("pid") and (s["start"], s["end"]) not in keys]
    if any(s["id"] not in reset for s in gone):            # 先全部检查完再动文件
        raise HTTPException(400, "有已经在编辑的段要改切点：先确认把它们的项目移到回收站")
    for s in gone:                                         # 挪到一半失败：挪走的那几个下次读时当没建过（_seq_view）
        pr = PROJECTS / s["pid"]
        if (pr / "project.json").is_file():
            _to_trash(pr, f"{d['name']}_段{s['id']}")
        s["pid"] = None
    try:
        SEQ.set_starts(d, starts)
    except ValueError as e:
        raise HTTPException(400, str(e))
    SEQ.save(root, d)
    return _seq_mix(sid)                                   # 锁定的段被重新剪掉了：整条预览跟着重拼


@app.post("/api/s/{sid}/auto")
def sequence_auto(sid: str):
    """重新自动分段（镜头切换 + 太长的平均切开）；已经在编辑的段原样保留。"""
    root = _seq(sid)
    d = SEQ.load(root)
    locked = [s for s in d["segments"] if s.get("pid")]
    st = set(SEQ.auto_starts(d.get("scene_cuts", []), d["n"], d["fps"]))
    for s in locked:
        st = {x for x in st if not s["start"] < x <= s["end"]} | {s["start"], s["end"] + 1}
    SEQ.set_starts(d, sorted(st))
    SEQ.save(root, d)
    return _seq_view(sid)


@app.post("/api/s/{sid}/seg/{gid}/use")
def sequence_use(sid: str, gid: str, body: dict = Body(...)):
    """导出全片时这一段用什么：auto（最新的视频结果）/ orig（原片）/ 某个结果 id。"""
    root = _seq(sid)
    d = SEQ.load(root)
    try:
        _, s = SEQ.seg_of(d, gid)
    except KeyError:
        raise HTTPException(404, "没有这一段")
    if s.get("lock"):
        raise HTTPException(409, "这一段已经锁定：先解锁再换")
    s["use"] = str(body.get("use") or "auto")
    SEQ.save(root, d)
    return _seq_view(sid)


@app.post("/api/s/{sid}/seg/{gid}/lock")
def sequence_lock(sid: str, gid: str, body: dict = Body(...)):
    """锁定 / 解锁这一段「全片里用」的结果。锁定后分段页的整条预览里这一段放它（后台重拼预览）。"""
    root = _seq(sid)
    d = SEQ.load(root)
    try:
        SEQ.set_lock(PROJECTS, d, gid, bool(body.get("lock")))
    except KeyError:
        raise HTTPException(404, "没有这一段")
    SEQ.save(root, d)
    return _seq_mix(sid)


@app.post("/api/s/{sid}/seg/{gid}/open")
def sequence_open(sid: str, gid: str):
    """编辑这一段：已经有项目就直接返回它的 id；没有就从母版剪出来建一个（后台任务，完成后界面打开它）。"""
    root = _seq(sid)
    d = SEQ.load(root)
    try:
        k, s = SEQ.seg_of(d, gid)
    except KeyError:
        raise HTTPException(404, "没有这一段")
    if s.get("pid") and (PROJECTS / s["pid"] / "project.json").is_file():
        return {"pid": s["pid"]}
    return start_job(lambda log: SEQ.open_segment(PROJECTS, sid, gid, log), f"准备第 {k + 1} 段")


@app.post("/api/s/{sid}/export")
def sequence_export(sid: str):
    _seq(sid)
    return start_job(lambda log: SEQ.export(PROJECTS, sid, log), "导出全片")


# ---------- 删除项目 / 长片段：不真删，整个文件夹挪到 projects/_回收站（要找回就挪回来）----------
TRASH = "_回收站"


def _to_trash(folder: Path, name: str):
    """把一个项目 / 长片段文件夹挪进回收站。任务正在用它、或文件被占用（Windows）就报错，什么都不动。"""
    if JOB.get("busy") and JOB.get("project") == folder.name:
        raise HTTPException(409, "这个项目有任务正在跑：等它跑完或先停止再删")
    for k in (folder.name,):                                   # 放掉服务端手里的对象（遮罩缓存等），不然 Windows 上文件被占用挪不动
        p = _projects.pop(k, None)
        if p is not None:
            p._mask_cache.clear()
        _peeked.pop(k, None)
        if k in _recent:
            _recent.remove(k)
    trash = PROJECTS / TRASH
    trash.mkdir(exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]', "_", name)[:60]
    dest = trash / f"{safe}_{folder.name}_{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        folder.rename(dest)
    except OSError as e:
        raise HTTPException(409, f"挪不动（文件可能正被占用，关掉播放器 / 资源管理器里的预览再试）：{e}")
    return str(dest)


@app.delete("/api/p/{pid}")
def delete_project(pid: str):
    root = PROJECTS / pid
    if not re.fullmatch(r"[0-9A-Za-z-]+", pid) or not (root / "project.json").is_file():
        raise HTTPException(404, "项目不存在")
    name = json.loads((root / "project.json").read_text(encoding="utf-8")).get("name", pid)
    return {"moved_to": _to_trash(root, name)}


@app.delete("/api/s/{sid}")
def delete_sequence(sid: str):
    """长片段连同它各段的项目一起挪进回收站（各段项目先挪，再挪长片段）。"""
    root = _seq(sid)
    d = SEQ.load(root)
    moved = []
    for s in d.get("segments", []):
        pr = PROJECTS / s["pid"] if s.get("pid") else None
        if pr and (pr / "project.json").is_file():
            moved.append(_to_trash(pr, f"{d['name']}_段{s['id']}"))
    moved.append(_to_trash(root, d["name"]))
    return {"moved_to": moved}


@app.get("/api/s/{sid}/preview")
def sequence_preview(sid: str):
    """分段页播放用的小预览。不用 .mp4 / video/*（IDM 会拦截），前端按 blob 播放。"""
    root = _seq(sid)
    return FileResponse(SEQ.preview_file(PROJECTS, root, SEQ.load(root)), media_type="application/octet-stream",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/s/{sid}/strip.jpg")
def sequence_strip(sid: str, k: int = 40):
    """时间轴下面的缩略图条：整条视频平均取 k 张，横着拼成一张（缓存）。"""
    root = _seq(sid)
    k = int(np.clip(k, 8, 120))
    d = SEQ.load(root)
    src = SEQ.preview_file(PROJECTS, root, d)                # 有锁定的段：用拼好的整条预览
    out = root / (f"strip_mix_{k}.jpg" if src.name == "preview_mix.mp4" else f"strip_{k}.jpg")
    if not out.exists():
        dur = d["n"] / d["fps"]
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(src), "-vf",
                        f"fps={k / dur:.6f},scale=-2:72,tile={k}x1", "-frames:v", "1", "-q:v", "5", str(out)], capture_output=True)
        if not out.exists():
            raise HTTPException(500, "缩略图条没做出来")
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@app.post("/api/s/{sid}/reveal")
def sequence_reveal(sid: str):
    """在资源管理器里定位：最新导出的全片，没导出过就打开长片段文件夹。"""
    root = _seq(sid)
    d = SEQ.load(root)
    ex = d.get("exports") or []
    path = root / ex[-1]["file"] if ex and (root / ex[-1]["file"]).is_file() else root / "master.mp4"
    _reveal(path)
    return {"path": str(path)}


@app.delete("/api/p/{pid}/results/{rid}")
def result_delete(pid: str, rid: str):
    """只从标签栏去掉；视频文件留在 passes 文件夹里。"""
    p = proj(pid)
    _result(p, rid)
    p.data["results"] = [r for r in p.data.get("results", []) if r["id"] != rid]
    p.save()
    return {"ok": True}


@app.get("/api/p/{pid}/images_sig")
def image_sig(pid: str):
    """当前图层状态的签名，和每个图片标签是否还能用（fresh：取完后图层只挪了几个像素也算能用），
    以及合成会不会用上首帧 / 尾帧（anchors）。"""
    p = proj(pid)
    sig = IM.layer_sig(p)
    return {"sig": sig, "fresh": {it["id"]: IM.sig_close(IM.pick_sig(it), sig) for it in IM.items(p) if it.get("sig")},
            "stale_cands": {it["id"]: [c for c in it.get("cands", []) if c in (it.get("cand_sig") or {})
                                       and not IM.sig_close(it["cand_sig"][c], sig)] for it in IM.items(p)},
            "anchors": IM.anchors(p),
            # 首帧 / 尾帧的画面（base.png）是不是按现在的编辑取的：不是 = 界面上「刷新」按钮标出来
            "base_fresh": {it["id"]: it.get("base_key") == RE.base_key(p, it) for it in IM.items(p)
                           if it["kind"] in IM.STILLS and not it.get("base_upload")
                           and it.get("version", p.data["current"]) == p.data["current"]}}


@app.get("/api/p/{pid}/images/{iid}/region")   # 不带 .png：通用的 /images/{iid}/{name}.png 注册在前，会先匹配
def image_region(pid: str, iid: str, img_close: int = -1, img_grow: int = -1, img_edge: int = -1, img_mgrow: int = -1,
                 img_occ: int = -1, g: str = ""):
    """首帧 / 尾帧的补图范围叠加层（预览分辨率、画框大小，红通道 = 区号 × 60：2 空区、1 补图范围里的其余部分）。
    参数没给（-1）就用这张图存的值；按现在的视频编辑 + 单图覆盖算，和出候选时一样。"""
    p = proj(pid)
    it = IM.get(p, iid)
    if it["kind"] not in IM.STILLS:
        raise HTTPException(400, "只有首帧 / 尾帧 / 关键帧有补图范围")
    q = dict(img_close=img_close, img_grow=img_grow, img_edge=img_edge, img_mgrow=img_mgrow, img_occ=img_occ)
    o = {k: (q[k] if q[k] >= 0 else int(it.get(k, v))) for k, v in RE.IMG_MASK_DEFAULTS.items()}
    with _zone_lock:
        lab = RE.region_preview(p, it, o, p.pw() / p.data["w"])
    buf = io.BytesIO()
    Image.fromarray((lab * 60).astype(np.uint8)).save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/p/{pid}/images/{iid}/background_upload")
async def image_background_upload(pid: str, iid: str, request: Request):
    p = proj(pid)
    it = IM.get(p, iid)
    if it["kind"] not in IM.STILLS:
        raise HTTPException(400, "仅首尾帧 / 关键帧可换背景参考")
    try:
        img = IM.open_upload(await request.body())
    except (OSError, ValueError):
        raise HTTPException(400, "请选择可读取的图片")
    import uuid
    stamp = uuid.uuid4().hex
    target = IM.folder(p, iid) / "bg_local.png"
    temp = target.with_suffix("." + stamp + ".png")
    img.save(temp)
    temp.replace(target)
    it.update(bg_choice="local", bg_local=stamp, use_bg=True)
    p.save()
    return it


@app.post("/api/p/{pid}/images/{iid}/gen_opts")
def image_gen_opts(pid: str, iid: str, body: dict = Body(...)):
    """存这张图的图片生成设置（不出图）：遮罩选项、空区填法、PE、补全身体 / 部位、背景参考、描述、基础提示。"""
    p = proj(pid)
    it = IM.get(p, iid)
    for k in RE.IMG_MASK_DEFAULTS:
        if body.get(k) is not None:
            it[k] = max(0, int(body[k]))
    if body.get("fill") in RP.FILLS:
        it["fill"] = body["fill"]
    if body.get("repaint_feather") is not None:              # 局部重绘羽化（像素，只往范围里面）
        it["repaint_feather"] = int(np.clip(int(body["repaint_feather"]), 0, 100))
    if body.get("mode") in ("inpaint", "gen"):
        it["mode"] = body["mode"]
    if "bg_choice" in body:
        choice = body["bg_choice"]
        pn = p.data.get("panorama") or {}
        allowed = [*pn.get("candidates", []), "upload", "qwen_full", "filled"]
        if choice == "local" and it.get("bg_local"):
            pass
        elif choice and (choice not in allowed or not (PANO.folder(p) / (choice + ".png")).is_file()):
            raise HTTPException(400, "背景候选不存在")
        it["bg_choice"] = choice or None
        if body.get("use_bg"):
            PANO.to_frame(p, it["frame"], IM.folder(p, iid), it["bg_choice"])
    for k, key in (("pe", "pe"), ("body", "body"), ("use_bg", "use_bg")):
        if body.get(k) is not None:
            it[key] = bool(body[k])
    for k, key in (("body_parts", "body_parts"), ("desc", "desc"), ("sys", "sys")):
        if body.get(k) is not None:
            it[key] = str(body[k]).strip()
    p.save()
    return it


@app.get("/api/projects")
def list_projects():
    out = []
    for d in sorted(PROJECTS.iterdir(), reverse=True):
        if (d / "project.json").exists():
            p = proj(d.name)
            out.append({"id": p.data["id"], "name": p.data["name"], "current": p.data["current"], "n": p.data["n"],
                        "w": p.data["w"], "h": p.data["h"], "layers": len(p.objects_in(p.data["current"])),
                        "results": len(p.data.get("results", [])), "seq": p.data.get("seq"),   # 长片段的某一段（打开对话框里不单独列）
                        "time": time.strftime("%m-%d %H:%M", time.localtime((d / "project.json").stat().st_mtime))})
    return out


_PICK_SCRIPT = r"""
import json
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
try:
    path = filedialog.askopenfilename(parent=root, title="打开视频", filetypes=[
        ("视频文件", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mpeg *.mpg *.wmv *.ts"),
        ("所有文件", "*.*")])
    print(json.dumps({"path": path}, ensure_ascii=True))
finally:
    root.destroy()
"""


@app.post("/api/local/video-picker")
def video_picker(request: Request):
    """「打开视频」没填路径时：在本机弹系统的选文件窗口，返回选的路径（取消 = 空）。
    Tk 放在独立进程的主线程里跑（在服务线程里建 / 销毁 Tk 会崩）。只接受本页面发起的请求，别的网站唤不起本机窗口。"""
    origin = request.headers.get("origin")
    if (origin and origin != f"http://{request.headers.get('host', '')}") or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "只能从 ReFrameVideo 页面打开")
    r = subprocess.run([sys.executable, "-c", _PICK_SCRIPT], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode:
        raise HTTPException(500, "无法打开文件窗口，请手动输入视频路径")
    return json.loads(r.stdout.decode("utf-8", "replace"))


@app.post("/api/projects")
def create_project(body: dict = Body(...)):
    path = str(body.get("video_path") or "").strip().strip('"')
    try:
        p = Project.create(PROJECTS, path)
    except FileNotFoundError:
        raise HTTPException(400, f"找不到视频：{path}")
    _projects[p.data["id"]] = p
    return p.data


@app.get("/api/p/{pid}")
def get_project(pid: str):
    p = proj(pid)
    pn = p.data.get("panorama")
    if pn and "extent" not in pn:                         # 全景图在画面坐标里的范围：画布上画原片外的背景用
        r = PANO.load(p)
        if r is not None:
            cr = p.crop_rows()
            pn["extent"] = pano_extent(r["T"], r["Hs"], r["img"].shape[1::-1],
                                       (p.data["w"], cr[1] - cr[0] if cr else p.data["h"]))
            p.save()
    for o in p.objects_in(p.data["current"]):
        if not ghost_path(p, o, o["end"]).exists():
            warm_ghosts(p, o)
    if p.data.pop("pending", None) is not None:            # 旧的「去除」流程留下的挑锚点状态：已经不用了
        p.save()
    if pn:                                                 # 背景图实际存在哪几张：图片结果按这个列候选，不会列到不存在的文件
        d = PANO.folder(p)
        pn["files"] = [n for n in ["qwen_full", "filled", "upload", *(pn.get("candidates") or [])]
                       if (d / f"{n}.png").is_file()]
    return p.data


@app.get("/api/p/{pid}/cache")
def get_cache(pid: str):
    """缓存占多少（设置里显示）。清掉不影响项目和结果，只是下次打开要重算。"""
    p = proj(pid)
    return {"size": p.cache_bytes(), "dir": p.cache_dir().name}


@app.post("/api/p/{pid}/cache/clear")
def clear_cache(pid: str):
    p = proj(pid)
    return {"freed": p.clear_cache()}


def _check_version(p, vid):
    """版本号必须是这个项目里有的（不然 p.video(vid) 会去读一个不存在的文件夹）。"""
    if not any(v["id"] == vid for v in p.data["versions"]):
        raise HTTPException(400, f"没有这个版本：{vid}")
    return vid


@app.post("/api/p/{pid}/current")
def set_current(pid: str, body: dict = Body(...)):
    p = proj(pid)
    p.data["current"] = _check_version(p, str(body.get("version") or ""))
    p.save()
    return p.data


@app.post("/api/p/{pid}/versions")
def duplicate_version(pid: str, body: dict = Body(default={})):
    """复制版本（默认复制当前版本）；fresh = 用原片视频新建一个空版本（不带图层）。新版本设成当前。"""
    p = proj(pid)
    label = str(body.get("label") or "").strip()
    if len(label) > 60:
        raise HTTPException(400, "名字最多 60 个字")
    try:
        fresh = bool(body.get("fresh"))
        p.duplicate_version("v0" if fresh else (body.get("from") or p.data["current"]), label or None, layers=not fresh)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return p.data


@app.post("/api/p/{pid}/versions/{vid}")
def rename_version(pid: str, vid: str, body: dict = Body(...)):
    """视频版本改名（只改显示的名字，id 和文件不变）。"""
    p = proj(pid)
    v = next((v for v in p.data["versions"] if v["id"] == vid), None)
    label = str(body.get("label", "")).strip()
    if v is None:
        raise HTTPException(404, "没有这个版本")
    if not label or len(label) > 60:
        raise HTTPException(400, "名字不能为空，最多 60 个字")
    v["label"] = label
    p.save()
    return p.data


@app.delete('/api/p/{pid}/versions/{vid}')
def delete_version(pid: str, vid: str):
    from contextlib import ExitStack
    with jobs.LOCK:
        if JOB['busy']:
            raise HTTPException(409, '任务运行中，请完成或停止任务后再删除版本')
        p = proj(pid)
        with ExitStack() as stack:
            for o in sorted(p.objects_in(vid), key=lambda o: o['id']):
                stack.enter_context(object_asset_lock(p, o['id']))
            try:
                p.delete_version(vid)
            except ValueError as e:
                raise HTTPException(409, str(e))
    return p.data


# ---------- 画面 ----------

@app.get("/api/p/{pid}/v/{vid}/clip")
def version_clip(pid: str, vid: str):
    """某个视频版本的整段视频（「原视频」标签播放用）。不用 .mp4 / video/* 的地址和类型：下载工具（IDM）会拦截，前端按 blob 播放。"""
    f = proj(pid).video(vid)
    if not f.exists():
        raise HTTPException(404, "没有这个版本的视频")
    return FileResponse(f, media_type="application/octet-stream")


@app.get("/api/p/{pid}/v/{vid}/frame/{i}.jpg")
def frame(pid: str, vid: str, i: int):
    f = proj(pid).proxy_dir(vid) / f"{i + 1:05d}.jpg"
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "max-age=86400"})


def hex_rgb(c):
    return tuple(int(c[k:k + 2], 16) for k in (1, 3, 5))


@app.get("/api/p/{pid}/overlay/{vid}/{i}.png")
def overlay(pid: str, vid: str, i: int, lift: str = "", tint: int = 0, sel: str = "", hide: str = ""):
    """叠加层（RGBA）：lift 里的图层（被移动、变换或隐藏）在原位置画棋盘格空洞；tint=1 时其余物体画彩色遮罩
    （颜色跟着图层变换走，画在物体现在的位置上）。"""
    p = proj(pid)
    lifted = set(lift.split(","))
    rgba = None
    k = p.pw() / p.data["w"]                                        # 预览分辨率 / 原画面
    S = np.diag([k, k, 1.0])
    for o in p.objects_in(vid):
        if o.get("prefill") or o.get("protect") or not (o["start"] <= i <= o["end"]):
            continue
        is_lift = o["id"] in lifted
        if not is_lift and not tint:
            continue
        m = p.proxy_mask(o["id"], i)
        if rgba is None:
            rgba = np.zeros((*m.shape, 4), np.uint8)
        if is_lift:
            yy, xx = np.indices(m.shape)
            checker = np.where(((yy // 8 + xx // 8) % 2) == 0, 205, 150).astype(np.uint8)[..., None]
            rgba[m] = np.concatenate([np.repeat(checker, 3, 2), np.full((*m.shape, 1), 255, np.uint8)], 2)[m]
        else:
            mm = m
            if not is_identity(o.get("tf")):                       # 移动 / 缩放 / 旋转过：遮罩跟着走
                M = (S @ np.vstack([p.layer_matrix(o), [0, 0, 1]]) @ np.linalg.inv(S))[:2].astype(np.float32)
                mm = cv2.warpAffine(m.astype(np.uint8), M, (m.shape[1], m.shape[0]), flags=cv2.INTER_NEAREST) > 0
            rgba[mm] = (*hex_rgb(o["color"]), 90)
    if rgba is None:
        rgba = np.zeros((1, 1, 4), np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgba).save(buf, "PNG", compress_level=1)
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "max-age=3600"})


def grow_of(o, grow=None):
    return int(o.get("grow", 0) if grow is None else grow)


def ghost_path(p, o, i, mode=None, grow=None):
    """图层帧的磁盘缓存：同一物体、同一种边缘、同一份 alpha、同样的外扩只生成一次。"""
    cr = p.crop_rows()
    import hashlib
    split = hashlib.sha1(json.dumps([(v["mask"], v.get("edits"), v.get("medits"), v.get("grow")) for v in o.get("parts", [])], sort_keys=True).encode()).hexdigest()[:10]
    c = PROJ.clean_opts(o)
    return p.cache_dir() / (f"layer_{o['id']}_split{split}_{edge_mode(o, mode)}_g{grow_of(o, grow)}_{o.get('alpha') or 'hard'}"
                               + ("_c" + "-".join(f"{v:g}" for v in c) if any(c) else "")
                               + (f"_c{cr[0]}-{cr[1]}" if cr else "") + (f"_w{p.pw()}" if p.pw() != PROJ.PROXY_W else "")) / f"{i:05d}.png"


def edge_mode(o, mode=None):
    mode = mode or o.get("edge", "sam")
    return mode if o.get("alpha") and mode in ("sam", "mix", "matte") else "sam"   # 没有 MatAnyone 结果时只能用硬边


def layer_alpha(p, o, i, mode=None, raw=False, grow=None):
    """按边缘模式算这一帧的透明通道（预览分辨率，uint8）。raw=True：不含手动修改（边缘外扩照常）。"""
    m = p.proxy_mask(o["id"], i, raw=raw, grow=grow)
    hard = (m * 255).astype(np.uint8)
    mode = edge_mode(o, mode)
    if mode == "sam":
        return hard
    a = p.proxy_alpha(o["id"], i, raw=raw, grow=grow)
    if o.get("parts"):
        a = a.copy()
        for part in o["parts"]:
            a[p.proxy_mask(part["id"], i)] = 0
    if mode == "matte":
        return a
    k = lambda r: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    core = cv2.erode(hard, k(2))                        # 内部（SAM 往里缩 2 像素）一律不透明
    band = cv2.dilate(hard, k(5)) > 0                   # 只在 SAM 外扩 5 像素以内用 MatAnyone 的柔边
    return np.maximum(core, np.where(band, a, 0)).astype(np.uint8)


_ghost_locks = {}
_ghost_locks_guard = threading.Lock()


_object_asset_locks = {}


def object_asset_lock(p, oid):
    with _ghost_locks_guard:
        return _object_asset_locks.setdefault((str(p.root), oid), threading.RLock())


def render_ghost(p, o, i, mode=None, grow=None):
    with object_asset_lock(p, o['id']):
        p.obj(o['id'])  # Deleted objects must not recreate their cached files.
        return _render_ghost(p, o, i, mode, grow)


def _render_ghost(p, o, i, mode=None, grow=None):
    """每一帧只生成一次：同一个文件加锁，后台预热线程和浏览器请求不会同时写。
    （Windows 上覆盖一个正在被读取的文件会“拒绝访问”，所以绝不能写第二次。）"""
    path = ghost_path(p, o, i, mode, grow)
    if path.exists():
        return path
    with _ghost_locks_guard:
        lock = _ghost_locks.setdefault(str(path), threading.Lock())
    with lock:
        if path.exists():                               # 等锁的时候别人已经生成好了
            return path
        a = layer_alpha(p, o, i, mode, grow=grow)
        img = np.array(Image.open(p.proxy_dir(o["version"]) / f"{i + 1:05d}.jpg").convert("RGB"))
        img = cv2.resize(img, a.shape[::-1])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.stem + ".tmp")
        Image.fromarray(np.dstack([img, a])).save(tmp, "PNG", compress_level=1)
        tmp.replace(path)                               # 先写临时文件再改名：别人永远读不到半个文件
    return path


def warm_ghosts(p, o):
    """后台提前生成整段图层帧，打开项目时直接读缓存。"""
    def run():
        for i in range(o["start"], o["end"] + 1):
            if not any(item['id'] == o['id'] for item in p.data['objects']):
                return
            try:
                render_ghost(p, o, i)
            except Exception:                           # 单帧出错不影响其他帧；浏览器请求这一帧时会再生成
                traceback.print_exc()
    threading.Thread(target=run, daemon=True).start()


@app.get("/api/p/{pid}/bgthumb/{vid}.png")
def bg_thumb(pid: str, vid: str, v: str = ""):
    """背景图层缩略图：第 0 帧，所有物体的位置挖成棋盘格。"""
    p = proj(pid)
    img = np.array(Image.open(p.proxy_dir(vid) / "00001.jpg").convert("RGB"))
    holes = np.zeros(img.shape[:2], bool)
    for o in p.objects_in(vid):
        if o.get("prefill") or o.get("protect"):
            continue
        if o["start"] <= 0 <= o["end"]:
            holes |= cv2.resize(p.proxy_mask(o["id"], 0).astype(np.uint8), img.shape[1::-1]) > 0
    yy, xx = np.indices(holes.shape)
    checker = np.where(((yy // 16 + xx // 16) % 2) == 0, 90, 60).astype(np.uint8)
    img[holes] = np.repeat(checker[..., None], 3, 2)[holes]
    small = cv2.resize(img, (120, round(img.shape[0] * 120 / img.shape[1])), interpolation=cv2.INTER_AREA)
    buf = io.BytesIO()
    Image.fromarray(small).save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/p/{pid}/ghost/{oid}/{i}.png")
def ghost(pid: str, oid: str, i: int, e: str = "", g: str = ""):
    """图层帧：物体本身的像素 + 透明背景（预览分辨率）。e = 边缘模式、g = 边缘外扩（以请求为准，不等设置保存）。"""
    p = proj(pid)
    return FileResponse(render_ghost(p, p.obj(oid), i, e or None, int(g) if g else None), media_type="image/png",
                        headers={"Cache-Control": "max-age=86400"})


# ---------- 分割 / 操作 ----------

@app.post("/api/p/{pid}/trim")
def trim_project(pid: str, body: dict = Body(...)):
    """时间轴「切出选区」：第 start–end 帧另存成新项目并打开（原项目不动；当前版本的图层、画幅带过去）。"""
    p = proj(pid)
    a, b = int(body["start"]), int(body["end"])
    if a > b or a < 0 or b >= int(p.data["n"]):
        raise HTTPException(400, "帧范围不对")
    if a == 0 and b == int(p.data["n"]) - 1:
        raise HTTPException(400, "选区就是整段视频，不用切")
    p.log_action("trim", start=a, end=b)
    return start_job(lambda log: dict(open_project=p.trim_copy(PROJECTS, a, b, log).data["id"]), "切出选区", pid)


@app.post("/api/p/{pid}/segment")
def segment(pid: str, body: dict = Body(...)):
    p = proj(pid)
    text, start, end = body["text"].strip(), int(body["start"]), int(body["end"])
    matte = body.get("matte", True) is not False                 # MatAnyone2 柔边：界面勾选，默认做（旧请求没有这个字段 = 做）
    vid = p.data["current"]
    p.log_action("segment", text=text, start=start, end=end)

    def job(log):
        log(f"识别“{text}”（第 {start}–{end} 帧，{ENGINE.name} 引擎）")
        work = p.new_pass_dir()
        insts = ENGINE.segment(p.video(vid), text, start, end, p.data, work, log)
        for k, m in enumerate(insts):
            m[:start] = False
            m[end + 1:] = False
            name = text if len(insts) == 1 else f"{text} {k + 1}"
            o = p.add_object(name, m, vid, prompt=text)
            if o is None:
                log(f"{name}：遮罩为空，跳过")
                continue
            if not matte:
                log(f"{name}：没勾 MatAnyone2 柔边，边缘用 SAM 硬边")
            elif hasattr(ENGINE, "matte") and not ENGINE.has_node("MatAnyone2"):
                log(f"{name}：4090 没装 MatAnyone2（可选），边缘用 SAM 硬边")
            elif hasattr(ENGINE, "matte"):
                log(f"{name}：MatAnyone2 抠出半透明边缘…")
                p.save_alpha(o["id"], ENGINE.matte(p.video(vid), m, o["start"], o["end"], p.data, work, log))
            warm_ghosts(p, p.obj(o["id"]))
        log(f"找到 {len(insts)} 个")

    return start_job(job, "物体识别", pid)


@app.get("/api/has_nodes")
def has_nodes(names: str = ""):
    """4090 上装没装这些节点（逗号分隔）：界面用来把没装的选项变灰。假引擎当全都装了。"""
    ks = [k for k in names.split(",") if k][:10]
    return {k: (ENGINE.has_node(k) if hasattr(ENGINE, "has_node") else True) for k in ks}


@app.post("/api/p/{pid}/segment_points")
def segment_points(pid: str, body: dict = Body(...)):
    """点选识别：第 frame 帧上的选中点 pos / 排除点 neg（原视频像素 [{x, y}]）→ SAM 3.1 出这一帧遮罩，往后跟踪到 end；
    back = 往前也跟踪到 start。和文字识别一样建一个图层、补 MatAnyone2 柔边。name 可空（默认「点选 N」）。"""
    p = proj(pid)
    vid = p.data["current"]
    n, W, H = p.data["n"], p.data["w"], p.data["h"]
    start, end, frame = int(body["start"]), int(body["end"]), int(body["frame"])
    if not 0 <= start <= frame <= end < n:
        raise HTTPException(400, "点选的那一帧要在识别范围里")
    pts = lambda k: [{"x": min(max(int(q["x"]), 0), W - 1), "y": min(max(int(q["y"]), 0), H - 1)} for q in body.get(k) or []][:32]
    pos, neg = pts("pos"), pts("neg")
    if not pos:
        raise HTTPException(400, "至少点一个要选中的点")
    back = bool(body.get("back")) and frame > start
    tracker = "matanyone" if body.get("tracker") == "matanyone" else "sam"
    matte = body.get("matte", True) is not False                 # SAM 跟踪后要不要补 MatAnyone2 柔边（MatAnyone2 跟踪本来就是柔边）
    if tracker == "matanyone" and hasattr(ENGINE, "has_node") and not ENGINE.has_node("MatAnyone2"):
        raise HTTPException(400, "4090 没装 MatAnyone2，只能用 SAM 3.1 跟踪")
    name = str(body.get("name") or "").strip()[:60]
    if not name:
        nums = [int(o["name"][3:]) for o in p.objects_in(vid) if o["name"].startswith("点选 ") and o["name"][3:].isdigit()]
        name = f"点选 {max(nums, default=0) + 1}"

    def job(log):
        work = p.new_pass_dir()
        m, alpha = ENGINE.segment_points(p.video(vid), frame, pos, neg, start, end, back, p.data, work, log, tracker=tracker)
        m[:start if back else frame] = False
        m[end + 1:] = False
        o = p.add_object(name, m, vid, prompt="点选")
        if o is None:
            log(f"{name}：没识别出东西（换个位置点，或多点几个选中点）")
            return
        if alpha is not None:                                       # MatAnyone2 跟踪：柔边已经有了，不用再跑
            alpha[:start if back else frame] = 0
            alpha[end + 1:] = 0
            p.save_alpha(o["id"], alpha)
        elif not matte:
            log(f"{name}：没勾 MatAnyone2 柔边，边缘用 SAM 硬边")
        elif hasattr(ENGINE, "matte") and not ENGINE.has_node("MatAnyone2"):
            log(f"{name}：4090 没装 MatAnyone2（可选），边缘用 SAM 硬边")
        elif hasattr(ENGINE, "matte"):
            log(f"{name}：MatAnyone2 抠出半透明边缘…")
            p.save_alpha(o["id"], ENGINE.matte(p.video(vid), m, o["start"], o["end"], p.data, work, log))
        warm_ghosts(p, p.obj(o["id"]))
        log(f"点选识别完成：{name}（第 {o['start']}–{o['end']} 帧）")

    return start_job(job, "点选识别", pid)


@app.post("/api/p/{pid}/obj/{oid}/parts")
def segment_part(pid: str, oid: str, body: dict = Body(...)):
    p = proj(pid)
    parent = p.obj(oid)
    text = str(body.get("text", "")).strip()
    if not text or len(text) > 200 or parent.get("shape") or parent.get("parent_id"):
        raise HTTPException(400, "请选择人物或物体图层，并填写部位名称")
    vid = parent["version"]
    start, end = parent["start"], parent["end"]
    def job(log):
        work = p.new_pass_dir()
        log(f"识别「{parent['name']}」的「{text}」")
        frames = p.frames(vid).copy()
        parent_mask = p.masks(oid, grow=0).copy()
        frames[~parent_mask] = 128
        source = work / "parent_input.mp4"
        write_video(source, frames, p.data["fps"])
        (work / "part_prompt.txt").write_text(text, encoding="utf-8")
        del frames, parent_mask
        insts = ENGINE.segment(source, text, start, end, p.data, work, log, filter_fragments=False)
        with object_asset_lock(p, oid):
            current = p.obj(oid)
            mask = np.zeros((p.data["n"], p.data["h"], p.data["w"]), bool)
            for instance in insts:
                mask |= instance
            mask &= p.masks(oid, grow=0)
            mask[:start] = False
            mask[end + 1:] = False
            if not mask.any():
                raise ValueError("该图层范围内没有识别到这个部位，请换一种描述")
            mid = p.save_masks(mask)
            part = dict(id=uuid.uuid4().hex[:8], name=text, mask=mid, grow=PROJ.NEW_LAYER_GROW)   # 新部位：收缩 / 扩展默认 0
            p.init_part(current, part)
            current.setdefault("parts", []).append(part)
            p.data["rev"] = uuid.uuid4().hex[:6]
            p.save()
            log("从属图层已保存，按 M 可检查重绘区域")
    return start_job(job, "从属图层识别", pid)


@app.delete("/api/p/{pid}/obj/{oid}")
def delete_obj(pid: str, oid: str):
    p = proj(pid)
    o = p.obj(oid)
    gone = {k: o.get(k) for k in ("id", "name", *LAYER_FIELDS) if k in o}
    with object_asset_lock(p, oid):
        p.delete_object(oid)
    p.log_action("delete_layer", layer=gone)
    return p.data


@app.post("/api/p/{pid}/merge")
def merge_objs(pid: str, body: dict = Body(...)):
    """把几个图层合并成一个（比如分开抠的人和她手里的纸）：遮罩逐帧取并集（各自的边缘外扩、手动修改都算进去），
    柔边取最大；变换、边缘模式、颜色、层级用「主图层」的。原来的图层从列表里去掉（遮罩文件留在磁盘上）。
    主图层（2026-11-15 用户定）：body.main 指定的；没指定时 = 改过位置大小的那个（几个都改过且一样也行）；都没改过 = 面积最大的。
    几个都改过、改得不一样又没指定：409，前端让用户选。合并后的外框中心变了：平移按旧外框中心补回，主图层的像素落在原来的位置。"""
    p = proj(pid)
    os_ = sorted((p.obj(i) for i in body.get("oids", [])), key=lambda o: o.get("z") or 0)
    if len(os_) < 2 or len({o["version"] for o in os_}) > 1:
        raise HTTPException(400, "选两个以上、同一个版本的图层")
    if any(o.get("parts") for o in os_):
        raise HTTPException(400, "请先移除从属图层再合并，以免改变局部重绘的归属")
    parents = {o.get("parent_id") for o in os_}
    if len(parents) > 1:
        raise HTTPException(400, "请选择同一父图层下的图层合并")
    area = {o["id"]: int(p.masks(o["id"]).sum()) for o in os_}
    os_.sort(key=lambda o: -area[o["id"]])
    edited = [o for o in os_ if not is_identity(o.get("tf"))]
    if body.get("main"):
        top = next((o for o in os_ if o["id"] == body["main"]), None)
        if top is None:
            raise HTTPException(400, "主图层不在要合并的图层里")
    elif len({json.dumps(o["tf"], sort_keys=True) for o in edited}) > 1:
        raise HTTPException(409, "这几个图层的位置大小都改过、改得不一样：选一个按它的位置大小合并")
    else:
        top = (edited or os_)[0]
    os_.remove(top)
    os_.insert(0, top)
    m = np.zeros_like(p.masks(top["id"]))
    for o in os_:
        m |= p.masks(o["id"])
    alphas = []
    for o in os_:
        a = [p.proxy_alpha(o["id"], i) for i in range(p.data["n"])] if o.get("alpha") else None
        alphas.append(np.stack(a) if a else np.stack([p.proxy_mask(o["id"], i) * 255 for i in range(p.data["n"])]).astype(np.uint8))
    name = "+".join(o["name"] for o in os_)
    no = p.add_object(name, m, top["version"], color=top["color"], prompt=top.get("prompt"))
    if no is None:
        raise HTTPException(400, "这几个图层都是空的")
    try:
        _merge_finish(p, os_, top, no, alphas)
    except Exception:                                         # add_object 已经存过盘：后面出错就把新图层撤掉，不留重复图层
        p.data["objects"] = [o for o in p.data["objects"] if o["id"] != no["id"]]
        p.save()
        raise
    p.log_action("merge", layers=[dict(id=o["id"], name=o.get("name"), tf=o.get("tf")) for o in os_], main=top["id"],
                 result=dict(id=no["id"], name=no["name"], tf=no.get("tf")))
    return p.data


def _merge_finish(p, os_, top, no, alphas):
    """合并的后半段：新图层抄主图层的设置、补回变换中心、合并柔边、换掉原来的几个图层，存盘。"""
    for k in LAYER_FIELDS:
        if k not in ("name", "grow") and k in top:
            no[k] = json.loads(json.dumps(top[k]))
    no["grow"] = 0
    tf = no.get("tf") or {}                                   # 外框中心从主图层的换成合并后的：t' = t + (I − A)(c − c')
    if not is_identity(tf):
        A = layer_matrix(dict(tf=tf, bbox=[0, 0, 0, 0]))[:, :2].astype(float)
        c = np.array([(top["bbox"][0] + top["bbox"][2]) / 2, (top["bbox"][1] + top["bbox"][3]) / 2])
        c2 = np.array([(no["bbox"][0] + no["bbox"][2]) / 2, (no["bbox"][1] + no["bbox"][3]) / 2])
        dx, dy = (np.eye(2) - A) @ (c - c2)
        no["tf"] = dict(tf, x=float(tf.get("x", 0) + dx), y=float(tf.get("y", 0) + dy))
    if any(o.get("alpha") for o in os_):
        aid = uuid.uuid4().hex[:10]
        np.savez_compressed(p.root / "masks" / f"alpha_{aid}.npz", alpha=np.max(alphas, axis=0))
        no["alpha"] = aid
    ids = {o["id"] for o in os_}
    if top.get("parent_id"):
        parent = p.obj(top["parent_id"])
        no["parent_id"] = parent["id"]
        parent["parts"] = [o for o in parent["parts"] if o["id"] not in ids] + [no]
        ids.add(no["id"])
    p.data["objects"] = [o for o in p.data["objects"] if o["id"] not in ids]
    p.save()


@app.get("/api/p/{pid}/obj/{oid}/stats")
def obj_stats(pid: str, oid: str):
    """这个图层让多少画面要重绘：它造成的空区（原位置的洞、外延生成、包围框重绘、出画补全，和生成同一套规则）
    在哪些帧、最多 / 平均占原片多少。四分之一分辨率、每 3 帧取一帧估算，几秒内出结果。"""
    p = proj(pid)
    p.obj(oid)
    from regions import _Layout
    try:
        L = _Layout(p, None, allow_unchanged=True, scale=0.25)
    except ValueError:
        return {"frames": 0}
    n = p.data["n"]
    hit, fr = [], []
    for i in range(0, n, 3):
        m = L.holes_by_layer(i).get(oid)
        if m is not None and m.any():
            hit.append(i)
            fr.append(float(m.mean()))
    if not hit:
        return {"frames": 0}
    return {"frames": len(hit) * 3, "start": hit[0], "end": min(n - 1, hit[-1] + 2),
            "max": round(max(fr) * 100, 1), "mean": round(sum(fr) / len(fr) * 100, 1)}


@app.post("/api/p/{pid}/gen_prompt/preview")
def gen_prompt_preview(pid: str, body: dict = Body(...)):
    """视频生成面板：按现在勾的参考图算出默认系统提示词（和生成时 repair_prompt.system_prompt 同一套）。
    body = {version, anchors: {first, last, "key:<标签 id>"}, use_bg, cards: [图层 id]}；首帧 / 尾帧 / 关键帧要真有选用的图才算。"""
    import images as IM
    from repair_prompt import system_prompt
    p = proj(pid)
    vids = {v["id"] for v in p.data.get("versions", [])}
    vid = body.get("version") if body.get("version") in vids else p.data["current"]
    a = body.get("anchors") or {}
    has = lambda k: bool(a.get(k)) and bool(IM.picked_path(p, k, vid=vid))
    cards = [oid for oid in body.get("cards") or [] if any(o["id"] == oid for o in p.objects_in(vid))]
    has_bg = bool(body.get("use_bg")) and PANO.load(p) is not None
    keys = [IM.still_index(p, it) / p.data["fps"] for it in IM.keys(p, vid) if a.get(f"key:{it['id']}") and IM.item_pick_path(p, it)]
    prompt, _ = system_prompt(p, has("first"), has("last"), has_bg, cards, vid, relight=float(body.get("body_redraw") or 0) > 0, keys=keys)
    return {"system": prompt}


@app.post("/api/p/{pid}/gen_prompt")
def gen_prompt_save(pid: str, body: dict = Body(...)):
    """视频生成面板的提示词（存进项目，下次打开还在）：user = 用户提示词，system = 改过的系统提示词（空 = 用默认），
    api = 切到云端 API 时原样发给平台的提示词。"""
    p = proj(pid)
    g = {k: str(body.get(k) or "")[:20000] for k in ("user", "system", "api")}   # api = 视频生成走 API 时的提示词
    p.data["gen_prompt"] = {k: v for k, v in g.items() if v.strip()}
    p.save()
    return {"ok": True, "gen_prompt": p.data["gen_prompt"]}


SHAPE_NAME = "空区蒙版"
PREFILL_NAME = "背景预填"
BGSWAP_NAME = "替换背景"                                            # 替换背景：整幅的空区蒙版（看得见的主体以外全是空区，生成时整片重画）
PROTECT_NAME = "保护蒙版"                                            # 保护蒙版：画的地方一律不重绘（regions._Layout.protect → 区号本体）                                            # 背景预填图层：画的范围里原本就是空区的地方，粗剪 / <Video 1> 用背景图填，不涂灰（仍然重绘）


@app.post("/api/p/{pid}/shape")
async def add_shape(pid: str, request: Request):
    """图形蒙版：用户在画面上画的方块 / 圆形等（矩形 / 椭圆 / 多边形 / 套索 / 画笔）= 纯空区，生成时和空洞一样补。
    每画一次就建一个新的固定蒙版图层「空区蒙版 N」（2026-11-08 用户定：不再并进同一个图层），默认作用于所有帧，
    时间轴上拖它的色条两端改帧范围（/shape_range）。这个图层默认隐藏 + 锁定：画面上是空洞，生成时涂灰重绘，不带原视频像素；
    眼睛 = 启用 / 停用（字段 off），不是显示原片像素。
    请求体 = 蒙版图片（白 = 空区，任意尺寸，按原视频大小缩放）。
    ?kind=prefill：建「背景预填 N」图层（字段 prefill）：不新增空区，只把范围里本来就是空区的地方改用背景图预填（generate.prefill）。
    ?kind=bgswap：建「替换背景 N」图层（2026-11-18 用户定）= 整幅的空区蒙版，不用请求体。空洞本来就扣掉看得见的图层
    （regions._sources 的 & ~covered），所以结果 = 主体以外全是空区、整片重画，主体不动。"""
    kind = request.query_params.get("kind")
    prefill, protect, bgswap = kind == "prefill", kind == "protect", kind == "bgswap"
    p = proj(pid)
    H, W, n = p.data["h"], p.data["w"], p.data["n"]
    if bgswap:
        m = np.ones((H, W), bool)
    else:
        data = await request.body()
        if not data:
            raise HTTPException(400, "没有收到蒙版")
        m = np.array(Image.open(io.BytesIO(data)).convert("L"))
        if m.shape != (H, W):
            m = cv2.resize(m, (W, H), interpolation=cv2.INTER_NEAREST)
        m = m > 127
        if not m.any():
            raise HTTPException(400, "画的形状是空的")
    vid = p.data["current"]
    name = PREFILL_NAME if prefill else PROTECT_NAME if protect else BGSWAP_NAME if bgswap else SHAPE_NAME
    nums = [int(x) for o in p.objects_in(vid) if o.get("shape")
            for x in [o["name"][len(name):].strip()] if o["name"].startswith(name) and x.isdigit()]
    no = p.add_object(f"{name} {max(nums, default=0) + 1}", np.broadcast_to(m, (n, H, W)).copy(), vid)
    no.update(shape=True, visible=False, locked=True, ring_redraw=0, hole_rect=0)   # 纯空区：隐藏 = 空洞，锁定 = 不会被拖走；主体的蒙版默认值不适用
    if prefill:
        no["prefill"] = True
    if protect:
        no["protect"] = True
    if bgswap:                                            # 换背景：旧背景图会把旧背景画回来，首帧 / 尾帧自动不带背景图、空区涂灰（用户可以再勾回来）
        no["bgswap"] = True
        for it in p.data.get("images", []):
            if it.get("kind") in IM.STILLS and it.get("version") == vid:
                it["use_bg"] = False
                if it.get("fill") == "bg":
                    it["fill"] = "grey"
    p.save()
    return p.data


@app.post("/api/p/{pid}/obj/{oid}/keep")
async def add_keep_shape(pid: str, oid: str, request: Request):
    """主体局部保留（2026-11-11 用户定）：用户在画面上第 frame 帧、这个主体现在的位置上画的范围（请求体 = 原视频大小的蒙版图，
    白 = 范围）。按图层变换倒回原片坐标，裁成这一帧主体外接框里的一块存成 masks/keep_<id>.png（按 id 存、不随条目删除，撤销照样能用）。
    只存形状、返回 {id, frame}；条目由前端加进图层字段 keep_masks（这样能撤销）。"""
    p = proj(pid)
    o = p.obj(oid)
    if o.get("shape"):
        raise HTTPException(400, "蒙版图层不能加主体局部保留")
    data = await request.body()
    if not data:
        raise HTTPException(400, "没有收到蒙版")
    H, W, n = p.data["h"], p.data["w"], p.data["n"]
    i = int(np.clip(int(request.query_params.get("frame") or 0), 0, n - 1))
    m = np.array(Image.open(io.BytesIO(data)).convert("L"))
    if m.shape != (H, W):
        m = cv2.resize(m, (W, H), interpolation=cv2.INTER_NEAREST)
    # 画面上的位置 → 原片坐标（图层变换倒回去；从属图层含父图层的变换）
    src = cv2.warpAffine((m > 127).astype(np.uint8), p.layer_matrix(o), (W, H),
                         flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP) > 0
    body = p.masks(oid)[i]
    ys, xs = np.nonzero(body)
    if not len(xs):
        raise HTTPException(400, "这一帧没有这个主体：换到主体出现的帧再画")
    crop = src[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    if not (crop & body[ys.min():ys.max() + 1, xs.min():xs.max() + 1]).any():
        raise HTTPException(400, "画的范围没有碰到这个主体")
    kid = uuid.uuid4().hex[:8]
    (p.root / "masks").mkdir(exist_ok=True)
    cv2.imencode(".png", crop.astype(np.uint8) * 255)[1].tofile(str(p.root / "masks" / f"keep_{kid}.png"))
    return {"id": kid, "frame": i}


@app.get("/api/p/{pid}/keep/{kid}.png")
def get_keep_shape(pid: str, kid: str):
    """主体局部保留的形状（外接框里那一块，白 + 不透明 = 范围，其余透明）：前端按每帧主体外接框摆上去做预览。"""
    p = proj(pid)
    if not kid.isalnum():
        raise HTTPException(400, "id 不对")
    f = p.root / "masks" / f"keep_{kid}.png"
    if not f.is_file():
        raise HTTPException(404, "没有这个主体局部保留")
    m = cv2.imdecode(np.fromfile(str(f), np.uint8), cv2.IMREAD_GRAYSCALE)
    rgba = np.dstack([np.full_like(m, 255)] * 3 + [m])
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "max-age=86400"})


def _replace_shape(p, old, allm, vid):
    """空区蒙版换成新的遮罩：建新图层（换 id，图层帧缓存跟着作废），删掉旧的。"""
    no = p.add_object(old["name"] if old else SHAPE_NAME, allm, vid, color=old["color"] if old else None)
    if no is None:
        raise HTTPException(400, "这段帧里没有形状")
    no.update(shape=True, visible=False, locked=True, ring_redraw=0, hole_rect=0)   # 纯空区：隐藏 = 空洞，锁定 = 不会被拖走；主体的蒙版默认值不适用
    if old and old.get("off"):
        no["off"] = True
    for k in ("prefill", "protect", "bgswap"):
        if old and old.get(k):
            no[k] = True
    if old:
        no["z"] = old.get("z", no["z"])
        p.delete_object(old["id"])
    p.save()


@app.post("/api/p/{pid}/shape_range")
def shape_range(pid: str, body: dict = Body(...)):
    """固定蒙版只作用在第 start–end 帧（时间轴上拖色条两端）：这段帧里是画的形状，别的帧没有。body.oid = 哪个蒙版（旧前端不传 = 第一个）。"""
    p = proj(pid)
    vid = p.data["current"]
    old = next((o for o in p.objects_in(vid) if o.get("shape") and (not body.get("oid") or o["id"] == body["oid"])), None)
    if not old:
        raise HTTPException(400, "还没有空区蒙版")
    n = p.data["n"]
    s = int(np.clip(int(body.get("start", 0)), 0, n - 1))
    e = int(np.clip(int(body.get("end", n - 1)), s, n - 1))
    full = p.masks(old["id"])
    shape = full.any(0)                                             # 画的形状（每一帧都一样，取并集）
    allm = np.zeros_like(full)
    allm[s:e + 1] = shape
    _replace_shape(p, old, allm, vid)
    return p.data


@app.get("/api/p/{pid}/obj/{oid}/base/{i}.png")
def get_base_alpha(pid: str, oid: str, i: int, e: str = "", g: str = ""):
    """这一帧原始的抠图结果（SAM / MatAnyone，按边缘模式 e，不含手动修改），预览分辨率灰度图。调整抠图时当底图。"""
    p = proj(pid)
    a = layer_alpha(p, p.obj(oid), i, e or None, raw=True, grow=int(g) if g else None)
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/p/{pid}/obj/{oid}/edits/{i}.png")
def get_frame_edits(pid: str, oid: str, i: int):
    """这一帧的手动修改（原分辨率）：红 = 补上，绿 = 擦掉。"""
    p = proj(pid)
    add, erase = p.frame_edits(oid, i)
    rgb = np.zeros(add.shape + (3,), np.uint8)
    rgb[add, 0] = 255
    rgb[erase, 1] = 255
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/p/{pid}/obj/{oid}/edits/{i}")
async def set_frame_edits(pid: str, oid: str, i: int, request: Request):
    """保存这一帧的手动修改：请求体 PNG，红 = 补上，绿 = 擦掉（两张都空 = 还原成原始结果）。"""
    p = proj(pid)
    p.obj(oid)
    if not 0 <= i < p.data["n"]:
        raise HTTPException(400, "帧号超出范围")
    im = np.array(Image.open(io.BytesIO(await request.body())).convert("RGB"))
    w, h = p.data["w"], p.data["h"]
    if im.shape[:2] != (h, w):
        im = cv2.resize(im, (w, h), interpolation=cv2.INTER_NEAREST)
    o = p.set_frame_edits(oid, i, im[..., 0] > 127, im[..., 1] > 127)
    for d in p.cache_dir().glob(f"layer_{oid}_*"):       # 这一帧的图层缓存（各种边缘模式）作废
        for k in range(10):
            try:
                (d / f"{i:05d}.png").unlink(missing_ok=True)
                break
            except PermissionError:                             # Windows：正被读取时删不掉，稍等再删
                time.sleep(0.05)
    return o


LAYER_FIELDS = ("name", "tf", "z", "opacity", "visible", "locked", "off", "edge", "grow", "box_ext", "ext_masks", "keep_masks", "hole_prefill") + MASK_OPTS + PROJ.CLEAN_OPTS


@app.patch("/api/p/{pid}/obj/{oid}")
def update_obj(pid: str, oid: str, body: dict = Body(...)):
    """改图层属性：名字、变换、层级、不透明度、可见、锁定。"""
    p = proj(pid)
    o = p.obj(oid)
    for k in LAYER_FIELDS:
        if k in body:
            o[k] = body[k]
    p.save()
    if "edge" in body or "grow" in body:                  # 换了边缘 / 外扩：后台把整段图层帧先生成好
        warm_ghosts(p, o)
    return {k: o.get(k) for k in ("id",) + LAYER_FIELDS}


@app.post("/api/p/{pid}/layers")
def update_layers(pid: str, body: dict = Body(...)):
    """一次改多个图层（拖动排序、撤销/重做）：{"layers": [{"id":..., 字段...}, ...]}"""
    p = proj(pid)
    changes = []
    items = body.get("layers", [])
    ids = {x["id"] for o in p.data["objects"] for x in [o, *o.get("parts", [])]}
    missing = [str(it.get("id")) for it in items if it.get("id") not in ids]
    if missing:                                           # 先全部检查再改：不会改了一半就报错（剩下的半截改动被之后的保存写进盘）
        raise HTTPException(404, f"图层已经不在了：{', '.join(missing)}")
    for item in items:
        o = p.obj(item["id"])
        warm = any(k in item and item[k] != o.get(k) for k in ("edge", "grow"))
        diff = [k for k in LAYER_FIELDS if k in item and item[k] != o.get(k)]
        if diff:                                          # 操作记录：只记改了的字段
            changes.append(dict(id=o["id"], name=o.get("name"), before={k: o.get(k) for k in diff}, after={k: item[k] for k in diff}))
        for k in LAYER_FIELDS:
            if k in item:
                o[k] = item[k]
        if warm:
            warm_ghosts(p, o)
    p.save()
    if changes:
        p.log_action("layers", label=str(body.get("label") or "")[:120], changes=changes)
    return {"ok": True}


@app.get("/api/p/{pid}/hit/{vid}/{i}")
def hit(pid: str, vid: str, i: int, x: float, y: float):
    """画面上点一下：返回该点最上层的可见图层（考虑变换后的位置）。x/y 为视频像素。"""
    p = proj(pid)
    W, H = p.data["w"], p.data["h"]
    for o in sorted(p.objects_in(vid), key=lambda o: -(o.get("z") or 0)):
        if not o.get("visible", True) or not (o["start"] <= i <= o["end"]):
            continue
        M = np.vstack([p.layer_matrix(o), [0, 0, 1]])
        sx, sy, _ = np.linalg.inv(M) @ np.array([x, y, 1])      # 反变换回原画面坐标
        m = p.proxy_mask(o["id"], i)
        px, py = int(sx * m.shape[1] / W), int(sy * m.shape[0] / H)
        if 0 <= px < m.shape[1] and 0 <= py < m.shape[0]:
            r = 3                                               # 边缘放宽几个像素，细的部分也好点中
            if m[max(py - r, 0):py + r + 1, max(px - r, 0):px + r + 1].any():
                return {"id": o["id"]}
    return {"id": None}


@app.get("/api/p/{pid}/thumb/{oid}.png")
def thumb(pid: str, oid: str, e: str = "", g: str = ""):
    """图层缩略图：取物体面积最大的一帧，裁出来，透明背景。"""
    p = proj(pid)
    o = p.obj(oid)
    areas = [((b[2] - b[0]) * (b[3] - b[1]) if b else 0) for b in o["bboxes"]]
    i = int(np.argmax(areas))
    gr = int(g) if g else None
    m = p.proxy_mask(oid, i, grow=gr)
    a = layer_alpha(p, o, i, e or None, grow=gr)
    img = np.array(Image.open(p.proxy_dir(o["version"]) / f"{i + 1:05d}.jpg").convert("RGB"))
    img = cv2.resize(img, m.shape[::-1])
    rgba = np.dstack([img, a])
    ys, xs = np.nonzero(m)
    crop = rgba[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    s = 64 / max(crop.shape[:2])
    crop = cv2.resize(crop, (max(1, round(crop.shape[1] * s)), max(1, round(crop.shape[0] * s))), interpolation=cv2.INTER_AREA)
    buf = io.BytesIO()
    Image.fromarray(crop).save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "max-age=3600"})


# ---------- 背景全景图 ----------

@app.post("/api/p/{pid}/panorama")
def make_panorama(pid: str, body: dict = Body(default={})):
    """把当前版本整段视频的背景整合成一张全景大图；fill=true 时接着用 Qwen 补洞。"""
    p = proj(pid)
    return start_job(lambda log: PANO.build(p, ENGINE, body, log), "背景图提取", pid)


@app.post("/api/p/{pid}/panorama/fill")
def panorama_fill(pid: str, body: dict = Body(default={})):
    """背景图第二步：用 Qwen Image 2.1 补第一步提取出的背景中间层（物体身后的洞、原片外）。body.api = 走云端平台。"""
    p = proj(pid)
    if body.get("api"):
        if not str(body.get("prompt") or "").strip():
            raise HTTPException(400, "先写提示词")
        _need_api()
        return start_job(lambda log: PANO.fill_holes(p, ENGINE, body, log, api=ENGINE.api), "背景图生成（API）", pid)
    return start_job(lambda log: PANO.fill_holes(p, ENGINE, body, log), "背景图生成", pid)


@app.delete("/api/p/{pid}/panorama")
def panorama_delete(pid: str):
    """删除背景全景图（文件夹改名保留，想找回可以改回 panorama）。"""
    return PANO.remove(proj(pid))


@app.post("/api/p/{pid}/panorama/choose_extract")
def choose_extraction(pid: str, body: dict = Body(...)):
    p = proj(pid)
    pn = p.data.get("panorama") or {}
    name = body.get("name")
    if not isinstance(name, str) or name not in pn.get("extracts", {}):
        raise HTTPException(404, "底图不存在")
    pn["selected_extract"] = name
    p.save()
    return pn


@app.post("/api/p/{pid}/panorama/use")
def panorama_use(pid: str, body: dict = Body(...)):
    """选择后续（铺回视频、给 H3 当参考）使用哪张全景：qwen_full = Qwen 整张；filled = 只补洞。"""
    p = proj(pid)
    if not p.data.get("panorama") or body.get("use") not in ["qwen_full", "filled", "upload", *p.data["panorama"].get("candidates", [])] or not (PANO.folder(p) / (body["use"] + ".png")).is_file():
        raise HTTPException(400)
    p.data["panorama"]["use"] = body["use"]
    p.save()
    return p.data["panorama"]


@app.get("/api/p/{pid}/panorama/{name}.png")
def panorama_file(pid: str, name: str, w: int = 0):
    """name = plate 拼接原图 / holes 洞标红 / qwen_input 交给 Qwen 的输入 / qwen_edit Qwen 原始输出 / filled 最终全景。"""
    if name not in [*(proj(pid).data.get("panorama") or {}).get("extracts", {}), "plate", "holes", "qwen_input", "qwen_edit", "qwen_full", "filled", "upload", *(proj(pid).data.get("panorama") or {}).get("candidates", [])]:
        raise HTTPException(404)
    f = PANO.folder(proj(pid)) / f"{name}.png"
    if not f.exists():
        raise HTTPException(404, "还没生成")
    if not w:
        return FileResponse(f, media_type="image/png", headers={"Cache-Control": "no-cache"})
    im = Image.open(f)
    im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=90)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


# ---------- 画框（输出画面）----------

@app.get("/api/p/{pid}/letterbox")
def letterbox(pid: str):
    """画框「裁切黑边」：在原片里均匀抽 8 帧，检测上下黑边的行数（原片像素）。"""
    from plate import detect_letterbox
    p = proj(pid)
    n = p.data["n"]
    top, bottom = detect_letterbox([p.frame("v0", round(k * (n - 1) / 7)) for k in range(8)])
    return dict(top=top, bottom=bottom, w=p.data["w"], h=p.data["h"])


@app.post("/api/p/{pid}/frame")
def set_frame(pid: str, body: dict = Body(...)):
    p = proj(pid)
    fr = {k: int(round(float(body[k]))) for k in ("x", "y", "w", "h")}
    if fr["w"] < 16 or fr["h"] < 16:
        raise HTTPException(400, "画框太小")
    fr["ratio"] = str(body.get("ratio", "free"))
    c = body.get("crop")                                 # 裁切黑边：上下各裁掉几行（原片像素），黑边里的东西不属于任何图层
    if c and (int(c.get("t", 0)) or int(c.get("b", 0))):
        fr["crop"] = {"t": max(0, int(c.get("t", 0))), "b": max(0, int(c.get("b", 0)))}
    before = p.data.get("frame")
    p.data["frame"] = fr
    p.save()
    if before != fr:
        p.log_action("frame", before=before, after=fr)
    return fr


@app.post("/api/p/{pid}/trans")
def set_trans(pid: str, body: dict = Body(...)):
    """过渡设置（H3 画布像素）：grow = 重绘区往外扩多少（0 = 不过渡），avoid = 离本体多远不扩。影响叠加层和生成。"""
    p = proj(pid)
    p.data.pop("trans_frac", None)                       # 旧的按比例过渡（11-04 上午），不用了
    p.data["trans"] = {"grow": min(128, max(0, int(body.get("grow", 48)))), "avoid": min(128, max(-64, int(body.get("avoid", -16))))}
    if "frames" in body:
        p.data["trans"]["frames"] = min(16, max(0, int(body["frames"])))
        p.data["trans"]["blue"] = body.get("blue") is True
    p.save()
    return p.data["trans"]


@app.post("/api/p/{pid}/export_frame")
def export_frame(pid: str, body: dict = Body(default={})):
    """按画框导出当前版本：框里原片外的部分是空的（黑），不用背景图补（空就是空，只有生成时才补）。"""
    p = proj(pid)
    vid = _check_version(p, body.get("version") or p.data["current"])
    w, h = p.data["w"], p.data["h"]
    fr = p.data.get("frame") or dict(x=0, y=0, w=w, h=h)
    scale = float(body.get("scale", 1.0))

    def job(log):
        rect = (fr["x"], fr["y"], fr["w"], fr["h"])
        outside = not (fr["x"] >= 0 and fr["y"] >= 0 and fr["x"] + fr["w"] <= w and fr["y"] + fr["h"] <= h)
        log(f"读取 {vid}…")
        frames = p.frames(vid)
        n = len(frames)
        ow, oh = round(fr["w"] * scale) // 2 * 2, round(fr["h"] * scale) // 2 * 2   # 视频编码要求偶数
        log(f"画框 {fr['w']}x{fr['h']}（位置 {fr['x']}, {fr['y']}）→ 输出 {ow}x{oh}" + ("，原片外是空的（黑）" if outside else ""))
        out = np.zeros((n, oh, ow, 3), np.uint8)
        for i in range(n):
            im = reframe(frames[i], None, None, None, rect)
            out[i] = cv2.resize(im, (ow, oh), interpolation=cv2.INTER_AREA) if im.shape[1::-1] != (ow, oh) else im
            if i % 30 == 0:
                log(f"  {i}/{n}")
        d = p.root / "exports"
        d.mkdir(exist_ok=True)
        path = d / f"frame_{vid}_{ow}x{oh}_{fr['x']}_{fr['y']}.mp4"
        write_video(path, out, p.data["fps"], audio_from=p.video("v0"))
        Image.fromarray(out[n // 2]).save(path.with_suffix(".jpg"))
        log(f"导出完成：{path}")

    return start_job(job, "画框导出", pid)


@app.post("/api/p/{pid}/op")
def do_op(pid: str, op: dict = Body(...)):
    p = proj(pid)
    p.log_action("generate_video", op={k: v for k, v in op.items() if k not in ("prompt", "system")})
    return start_job(lambda log: run_op(p, ENGINE, op, log), "视频生成", pid)


@app.get("/api/p/{pid}/export/{vid}")
def export_path(pid: str, vid: str):
    return {"path": str(proj(pid).video(vid).resolve())}


@app.get("/api/job")
def job_status():
    return dict(JOB, now=time.time())


@app.post("/api/job/cancel")
def job_cancel():
    """停止当前任务：正在跑的 ComfyUI 任务立刻打断并移出队列，本地循环到下一个检查点退出。"""
    if not JOB["busy"]:
        return {"ok": False, "busy": False}
    request_cancel()
    if JOB.get("log") is not None:
        JOB["log"].append("收到「停止」，正在撤掉任务…")
    return {"ok": True}


@app.post("/api/job/queue/{qid}/remove")
def job_queue_remove(qid: int):
    """把排队中（还没开始）的任务移出队列。"""
    return {"ok": jobs.remove(qid), "queue": JOB["queue"]}


# ---------- H3 LoRA：从 ComfyUI 的 loras 文件夹里选，所有 H3 生成（去除 / 修改 / 合成）都用 ----------

@app.get("/api/preview")
def get_preview():
    """画布预览宽度（为了画布操作流畅，预览帧是缩小过的；生成照常用原分辨率）。"""
    return dict(w=PROJ.PREVIEW["w"], choices=list(PROJ.PREVIEW_CHOICES), default=PROJ.PROXY_W)


@app.post("/api/preview")
def set_preview(body: dict = Body(...)):
    w = int(body.get("w", PROJ.PROXY_W))
    if w not in PROJ.PREVIEW_CHOICES:
        raise HTTPException(400, f"只能选 {PROJ.PREVIEW_CHOICES}")
    PROJ.PREVIEW["w"] = w
    s = read_settings()
    s["preview_w"] = w
    write_settings(s)
    return get_preview()


@app.get("/api/seq_preview")
def get_seq_preview():
    """分段页整条预览的长边（设置 → 性能「分段预览清晰度」）。"""
    return {"long": SEQ.PREVIEW["long"], "choices": list(SEQ.PREVIEW_CHOICES)}


@app.post("/api/seq_preview")
def set_seq_preview(body: dict = Body(...)):
    v = int(body.get("long", 0))
    if v not in SEQ.PREVIEW_CHOICES:
        raise HTTPException(400, f"只能选 {SEQ.PREVIEW_CHOICES}")
    SEQ.PREVIEW["long"] = v
    s = read_settings()
    s["seq_preview"] = v
    write_settings(s)
    return get_seq_preview()


WORK_CHOICES = (1280, 1920, 2560, 0)


@app.get("/api/p/{pid}/history")
def history(pid: str, limit: int = 300):
    """「历史」面板：这个项目操作记录里最近的 limit 条，新的在前（只读当前那个文件；超过 5 MB 改名的旧文件不列）。"""
    f = proj(pid).root / PROJ.ACTION_LOG_FILE
    if not f.exists():
        return {"on": PROJ.ACTION_LOG["on"], "items": []}
    items = []
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(int(limit), 2000)):]:
        try:
            items.append(json.loads(line))
        except ValueError:
            pass                                              # 写到一半断电的半行：跳过
    return {"on": PROJ.ACTION_LOG["on"], "items": items[::-1]}


@app.get("/api/action_log")
def get_action_log():
    """设置「记录操作」：每个项目文件夹里的 操作记录log.jsonl（默认开）。"""
    return {"on": PROJ.ACTION_LOG["on"], "file": PROJ.ACTION_LOG_FILE}


@app.post("/api/action_log")
def set_action_log(body: dict = Body(...)):
    PROJ.ACTION_LOG["on"] = bool(body.get("on"))
    s = read_settings()
    s["action_log"] = PROJ.ACTION_LOG["on"]
    write_settings(s)
    return get_action_log()


@app.get("/api/clean_uploads")
def get_clean_uploads():
    """设置「任务结束后删除上传到 ComfyUI 的素材」（ComfyUI 的 input/reframe 里；默认开）。"""
    return {"on": ENG.CLEAN_UPLOADS["on"]}


@app.post("/api/clean_uploads")
def set_clean_uploads(body: dict = Body(...)):
    ENG.CLEAN_UPLOADS["on"] = bool(body.get("on"))
    s = read_settings()
    s["clean_uploads"] = ENG.CLEAN_UPLOADS["on"]
    write_settings(s)
    return get_clean_uploads()


@app.get("/api/work_long")
def get_work_long():
    """设置「导入视频的工作分辨率」：长边超过它的视频导入时先缩到它（0 = 原尺寸，不缩）。只影响之后新建的项目。"""
    return {"v": PROJ.WORK_LONG["v"]}


@app.post("/api/work_long")
def set_work_long(body: dict = Body(...)):
    v = int(body.get("v", 1920))
    if v not in WORK_CHOICES:
        raise HTTPException(400, f"只能选 {WORK_CHOICES}")
    PROJ.WORK_LONG["v"] = v
    s = read_settings()
    s["work_long"] = v
    write_settings(s)
    return get_work_long()


@app.get("/api/keep_projects")
def get_keep_projects():
    """设置「后台的项目标签」：1 = 切走就放掉（默认，省内存），2 = 最近 2 个项目常驻（来回切更快，多占内存）。"""
    return {"n": KEEP["n"]}


@app.post("/api/keep_projects")
def set_keep_projects(body: dict = Body(...)):
    n = int(body.get("n", 1))
    if n not in (1, 2):
        raise HTTPException(400, "只能选 1 或 2")
    KEEP["n"] = n
    s = read_settings()
    s["keep_projects"] = n
    write_settings(s)
    return get_keep_projects()


def _h3_res() -> dict:
    """画布分辨率的当前设置（三个模式共用一份）。"""
    return dict(mode=ENG.H3_MODE, modes=list(ENG.H3_MODES), short=ENG.H3_SHORT,
                shorts=list(ENG.H3_SHORTS), free=list(ENG.H3_FREE),
                preset=ENG.H3_PRESET, presets={k: (list(v) if v else None) for k, v in ENG.H3_PRESETS.items()})


def _h3_res_save(body: dict, s: dict) -> None:
    """把面板传来的 mode / short / free 写进引擎和 app_settings.json（非法值抛 ValueError）。"""
    if body.get("preset"):
        s["h3_preset"] = ENG.set_h3_preset(body["preset"])
    if body.get("mode"):
        s["h3_mode"] = ENG.set_h3_mode(body["mode"])
        s["h3_preset"] = ENG.H3_PRESET                             # 旧的 auto 会顺带改成 768p 预设
    if body.get("short"):
        s["h3_short"] = ENG.set_h3_short(body["short"])
    if body.get("free"):
        s["h3_free"] = list(ENG.set_h3_free(*body["free"]))
    if s.get("h3_mode") == "free" and not s.get("h3_free"):
        s["h3_free"] = list(ENG.H3_FREE)


def _video_workflows() -> dict:
    """视频生成栏「模型」下拉里的自己的视频工作流（workflows/video/*.json）：不合格的灰掉并写原因；现在选的（文件还在才算）。"""
    wfs = []
    for name in WF.video_names():
        try:
            WF.load_video(name)
            wfs.append(dict(name=name, available=True))
        except ValueError as e:
            wfs.append(dict(name=name, available=False, note=str(e)))
    cur = read_settings().get("video_workflow")
    return dict(workflows=wfs, workflow=cur if any(w["name"] == cur and w["available"] for w in wfs) else "")


@app.get("/api/h3_loras")
def get_h3_loras():
    """视频生成栏：H3 主模型和 LoRA 的可选项 + 现在选的；画布分辨率（默认 / 按短边 / 自由）；自己的视频工作流。"""
    if not isinstance(BASE, ComfyEngine):
        return dict(available=[], selected=[], models=[], model="", steps=getattr(BASE, "H3_STEPS", 20),
                    error="假引擎：没有模型 / LoRA", **_h3_res(), **_video_workflows())
    try:
        avail, models = BASE.list_loras(), BASE.list_unets()
    except Exception as e:
        return dict(available=[], selected=BASE.loras, models=[BASE.unet], model=BASE.unet, steps=BASE.H3_STEPS,
                    error=f"读不到 ComfyUI 的模型列表：{e}", **_h3_res(), **_video_workflows())
    return dict(available=avail, selected=BASE.loras, models=models, model=BASE.unet, steps=BASE.H3_STEPS,
                error=None, **_h3_res(), **_video_workflows())


def _video_workflow_save(body: dict, s: dict) -> None:
    """body["workflow"]：自己的视频工作流名字；"" = 用内置的 H3。不传 = 不改。"""
    if "workflow" not in body:
        return
    name = str(body.get("workflow") or "").strip()
    if not name:
        s.pop("video_workflow", None)
        return
    try:
        WF.load_video(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    s["video_workflow"] = name


@app.post("/api/h3_loras")
def set_h3_loras(body: dict = Body(...)):
    if not isinstance(BASE, ComfyEngine):                         # 假引擎：模型 / LoRA 不能改，画布分辨率是纯几何设置，照常存
        if not (body.get("mode") or body.get("short") or body.get("free") or "workflow" in body):
            raise HTTPException(400, "假引擎不支持 LoRA")
        try:
            s = read_settings()
            _h3_res_save(body, s)
            _video_workflow_save(body, s)
            write_settings(s)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return dict(selected=[], model="", steps=getattr(BASE, "H3_STEPS", 20), **_h3_res(), **_video_workflows())
    sel = []
    for l in body.get("loras", []):
        name = str(l.get("name", "")).strip()
        if name:
            sel.append(dict(name=name, strength=float(np.clip(float(l.get("strength", 1.0)), -2.0, 2.0))))
    BASE.loras = sel
    s = read_settings()
    s["h3_loras"] = sel
    if body.get("model"):                                         # H3 主模型（diffusion_models 里的文件名）
        BASE.unet = s["h3_model"] = str(body["model"]).strip()
    if body.get("steps"):                                         # 采样步数（加速 LoRA 用少步数）
        BASE.H3_STEPS = s["h3_steps"] = int(np.clip(int(body["steps"]), 1, 100))
    try:
        _h3_res_save(body, s)                                     # 画布分辨率：默认 / 按短边 / 自由
    except ValueError as e:
        raise HTTPException(400, str(e))
    _video_workflow_save(body, s)                                 # 自己的视频工作流（"" = 内置 H3）
    write_settings(s)
    return dict(selected=sel, model=BASE.unet, steps=BASE.H3_STEPS, **_h3_res(), **_video_workflows())


@app.get("/api/sam_models")
def get_sam_models():
    """识别物体面板：识别模型的可选项 + 现在选的。"""
    if not isinstance(BASE, ComfyEngine):
        return dict(models=[], model="", error="假引擎：没有识别模型")
    try:
        models = BASE.list_sams()
    except Exception as e:
        return dict(models=[BASE.SAM_CKPT], model=BASE.SAM_CKPT, error=f"读不到 ComfyUI 的模型列表：{e}")
    return dict(models=models, model=BASE.SAM_CKPT, error=None)


@app.post("/api/sam_models")
def set_sam_model(body: dict = Body(...)):
    if not isinstance(BASE, ComfyEngine):
        raise HTTPException(400, "假引擎不能选识别模型")
    name = str(body.get("model", "")).strip()
    if not name:
        raise HTTPException(400, "没选模型")
    BASE.SAM_CKPT = name
    s = read_settings()
    s["sam_model"] = name
    write_settings(s)
    return dict(model=name)


class NoCacheStatic(StaticFiles):
    """页面和脚本不缓存：更新代码后普通刷新即可。整个文件读进内存再发（不用中间件改流式响应的头：
    文件在发送途中被改动时会报 “Response content longer than Content-Length”）。"""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if isinstance(resp, FileResponse):
            full, _ = self.lookup_path(path)
            f = Path(full) / "index.html" if full and Path(full).is_dir() else Path(full) if full else None
            if f and f.is_file():
                return Response(f.read_bytes(), media_type=resp.media_type, headers={"Cache-Control": "no-store"})
        resp.headers["Cache-Control"] = "no-store"
        return resp


app.mount("/", NoCacheStatic(directory=APP_DIR.parent / "web", html=True), name="web")   # 前端（web/）和后端同一个端口

if __name__ == "__main__":
    print(f"Engine / 引擎：{ENGINE.name} {_comfy or ''}    Projects / 项目目录：{PROJECTS}")
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else int(os.environ.get("VE_PORT", 8765))
    print(f"Open / 浏览器打开 http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
