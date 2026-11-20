"""云端 API：图片生成和视频生成面板切到「API」时用（2026-11-18 用户定：只开这两处的口子，平台由用户自己填）。
软件不内置任何平台：用户在设置 → 云端 API 里按三种「接口格式」之一写一份平台配置（TEMPLATES 是空白模板），接口都是
edit_image / generate_video / check：
  openai_edits = OpenAI 改图那种：POST {base_url}/images/edits，multipart 上传图片，同步返回（OpenAICompat）
  json_sync    = JSON 同步：POST {base_url}{path}，图片放 JSON 里（base64 data URI），直接返回图（JsonSync）
  async_task   = 异步任务：先提交拿任务号，再轮询，完成后给链接（AsyncTask；配置里有 video_path 时也能做视频生成）
API 只发输入图 / 参考图 / 粗剪和用户写的提示词，效果不保证。别的步骤（看图写描述、识别、抠像、H3）一律走 ComfyUI。

API key：存在仓库根目录的 api_keys.local.json（每个平台一个，不进 git，不写进 app_settings.json），
从不发回浏览器（只告诉它有没有、尾号几位）。
代理：requests 会自动用环境变量 HTTPS_PROXY / HTTP_PROXY。
"""
import base64
import json
import io
from pathlib import Path

import cv2
import numpy as np
import requests
from PIL import Image

DEFAULTS = dict(provider="", base_url="", image_model="", image_size="match", video_model="", video_resolution="")
FORMATS = ("openai_edits", "json_sync", "async_task")
# 平台配置的字段（设置 → 云端 API →「接口配置」里照模板填，字段说明也在 docs/安装.md「云端 API」）：
#   base_url 平台地址；image_model 图片模型名；path 提交地址；image_field 图片放哪个字段；single_as_string 只有一张图时给字符串（否则总是数组）；
#   size：wxh_ark（宽x高，按模型的总像素范围放大 / 缩小，火山方舟 Seedream 的规则）/ wxh（原图宽x高）/ ratio（最接近的比例，如 16:9）/ none（不发）；
#   extra 额外固定发的字段；result 结果在返回值里的位置（点分路径，数字 = 第几个；指到的是含 b64_json / url 的对象或直接是网址）；
#   异步：task_id 任务号位置、poll 查询地址（{id} 换成任务号）、status 状态位置、done / failed 状态值、error 失败原因位置；
#   models_query 列模型时带的参数；max_images 最多几张图（原图 + 参考图，0 = 不限）。
#   视频（只 async_task）：video_model 默认模型、video_path 提交地址（不填 = 这个平台不做视频）、video_field / video_image_field 视频 / 参考图网址放哪个字段、
#   upload_video / upload_image 上传地址（素材要先传上去换网址）、upload_url 上传返回里网址的位置、video_result 结果视频网址位置、
#   video_extra 额外固定发的字段、video_resolutions 可选分辨率（发在 resolution 字段）、video_max_images 最多几张参考图、
#   video_limits 平台对输入视频的限制：min_s / max_s 时长秒数、min_ratio / max_ratio 宽高比、min_pixels 最少总像素、
#   min_side 最短边、min_fps 最低帧率、max_mb 上传大小。
TEMPLATES = {
    "openai_edits": dict(format="openai_edits", base_url="", image_model=""),
    "json_sync": dict(format="json_sync", base_url="", image_model="", path="", image_field="", single_as_string=False,
                      size="none", extra={}, result="", max_images=0),
    "async_task": dict(format="async_task", base_url="", image_model="", path="", image_field="", single_as_string=False,
                       size="none", extra={}, task_id="", poll="", status="", done=["completed"], failed=["failed"], error="",
                       result="", models_query={}, max_images=0,
                       video_model="", video_path="", video_field="", video_image_field="", upload_video="", upload_image="",
                       upload_url="", video_result="", video_extra={}, video_resolutions=[], video_max_images=0, video_limits={}),
}
CUSTOM = {}                                  # 用户的平台配置（server 从 app_settings.json 的 api.profiles 读进来）
RATIOS = ("1:1", "4:3", "3:4", "3:2", "2:3", "16:9", "9:16", "21:9", "9:21", "2:1", "1:2")


def profile(pid):
    """用户的这个平台配置；没有 = None（还没设平台）。"""
    return CUSTOM.get(pid)


# 旧版本内置过三个平台（2026-11-18 去掉）。升级时把用户正在用 / 存过 key 的那几个转成用户自己的平台（同一个 id，key 照用），不丢设置。
_OLD_PRESETS = {
    "openai": dict(name="OpenAI 兼容", format="openai_edits", base_url="https://api.openai.com/v1", image_model="gpt-image-1"),
    "ark": dict(name="火山方舟 Seedream", format="json_sync", base_url="https://ark.cn-beijing.volces.com/api/v3",
                image_model="doubao-seedream-5-5-261028", path="/images/generations", image_field="image",
                single_as_string=True, size="wxh_ark", extra={"response_format": "b64_json", "watermark": False}, result="data.0",
                max_images=10),
    "apimart": dict(name="APIMart", format="async_task", base_url="https://api.apimart.ai/v1", image_model="seedream-4.5",
                    path="/images/generations", image_field="image_urls", single_as_string=False, size="ratio",
                    extra={}, task_id="data.0.task_id", poll="/tasks/{id}", status="data.status", done=["completed"],
                    failed=["failed"], error="data.error.message", result="data.result.images.0.url.0",
                    models_query={"category": "image"}, max_images=3,
                    video_model="seedance-2-5-flash", video_path="/videos/generations", video_field="video_urls",
                    video_image_field="image_urls", upload_video="/uploads/videos?retention_days=3",
                    upload_image="/uploads/images?retention_days=3", upload_url="url", video_result="data.result.videos.0.url.0",
                    video_extra={"omni_reference_task_type": "edit", "size": "adaptive", "generate_audio": False, "watermark": False},
                    video_resolutions=["480p", "720p"], video_max_images=9,
                    video_limits=dict(min_s=4, max_s=30, min_ratio=0.4, max_ratio=2.5, min_pixels=409600, min_side=300,
                                      min_fps=24, max_mb=50)),
}


def migrate(cfg, profiles, keys):
    """旧设置 → 新设置：返回 (cfg, profiles, 改没改)。只转用到过的旧内置平台（正在选的、或存过 key 的）；
    正在选的那个带上用户改过的地址 / 模型名；改图 / 看图路由这些旧字段丢掉。"""
    cfg, profiles, changed = dict(cfg or {}), dict(profiles or {}), False
    current = cfg.get("provider") or ("openai" if cfg else "")
    for pid, old in _OLD_PRESETS.items():
        if pid in profiles or not (current == pid or pid in keys):
            continue
        pf = dict(old)
        if current == pid:
            pf.update({k: cfg[k] for k in ("base_url", "image_model") if cfg.get(k)})
        profiles[pid], changed = pf, True
    if cfg and not cfg.get("provider") and "openai" in profiles:
        cfg["provider"], changed = "openai", True
    for k in ("edit", "describe", "vlm_model", "key_env"):
        if k in cfg:
            cfg.pop(k)
            changed = True
    if cfg.get("provider") and cfg["provider"] not in profiles:
        cfg["provider"], changed = "", True
    return cfg, profiles, changed


def fetch(url, what, timeout=180):
    """下载平台给的结果网址：HTTP 出错（链接过期 / 403 …）直接报错，不把错误页当成图片 / 视频写下来。"""
    r = requests.get(url, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"{what}：下载结果失败（HTTP {r.status_code}）：{r.text[:200]}")
    return r.content


def dig(obj, path):
    """点分路径取值：data.0.url → obj["data"][0]["url"]；取不到返回 None。"""
    for k in str(path or "").split(".") if path else []:
        try:
            obj = obj[int(k)] if isinstance(obj, list) else obj[k]
        except (KeyError, IndexError, ValueError, TypeError):
            return None
    return obj


def check_profile(pf):
    """用户另存的平台配置：检查必填项，返回中文原因（None = 没问题）。"""
    if not isinstance(pf, dict) or pf.get("format") not in FORMATS:
        return f"format 只能是 {' / '.join(FORMATS)}"
    if not str(pf.get("base_url") or "").startswith(("http://", "https://")):
        return "base_url 要以 http:// 或 https:// 开头"
    if not str(pf.get("image_model") or "").strip():
        return "缺少：image_model"
    need = {"json_sync": ("path", "image_field", "result"), "async_task": ("path", "image_field", "task_id", "poll", "status", "result")}
    miss = [k for k in need.get(pf["format"], ()) if not pf.get(k)]
    return f"缺少：{'、'.join(miss)}" if miss else None
# 火山方舟 Seedream 的 size（宽x高）总像素范围（官方文档 2026-11）：5.5 / 5.0 pro / 5.0 flash = [921600, 4624220]；
# 4.5 / 5.0 lite = [3686400, 16777216]；4.0 = [921600, 16777216]。按模型名里的版本号选，认不出的按 5.5。
ARK_PIXELS = (("seedream-4-5", 3686400, 16777216), ("seedream-5-0-lite", 3686400, 16777216),
              ("seedream-4-0", 921600, 16777216))
ARK_PIXELS_DEFAULT = (921600, 4624220)
ARK_MAX_IMAGES = 10                          # 输入图上限（5.5 / 5.0 pro / flash 是 10，别的 14：按小的）
EDIT_SIZES = ((1536, 1024), (1024, 1536), (1024, 1024))   # OpenAI 图片编辑支持的尺寸


KEYS_FILE = Path(__file__).resolve().parent.parent / "api_keys.local.json"


def _load_keys():
    """api_keys.local.json → {平台: key}；文件不在 = {}；坏了（不是 JSON / 不是对象）抛 RuntimeError。"""
    if not KEYS_FILE.exists():
        return {}
    try:
        d = json.loads(KEYS_FILE.read_text(encoding="utf-8"))
    except ValueError as e:
        raise RuntimeError(f"{KEYS_FILE.name} 不是合法的 JSON（{e}）：修好或删掉它再存 key")
    if not isinstance(d, dict):
        raise RuntimeError(f"{KEYS_FILE.name} 内容不对（应该是 {{平台: key}}）：修好或删掉它再存 key")
    return d


def read_keys():
    try:
        return _load_keys()
    except (OSError, RuntimeError):
        return {}


def save_key(provider, key):
    """存（key 为空 = 删掉）这个平台的 API key。文件坏了就报错，不覆盖（免得把别的平台的 key 冲掉）；先写临时文件再换名。"""
    keys = _load_keys()
    key = str(key or "").strip()
    if key:
        keys[provider] = key
    else:
        keys.pop(provider, None)
    text = json.dumps(keys, ensure_ascii=False, indent=1)
    tmp = KEYS_FILE.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    from project import replace_retry
    replace_retry(tmp, KEYS_FILE, text)


class OpenAICompat:
    def __init__(self, cfg):
        self.cfg = {**DEFAULTS, **(cfg or {})}

    @property
    def name(self):
        return "openai-compatible"

    def key(self):
        """设置里给这个平台填的 key（api_keys.local.json）；没选平台 = ""。"""
        return read_keys().get(self.cfg["provider"], "") if self.cfg["provider"] else ""

    def _url(self, path):
        return self.cfg["base_url"].rstrip("/") + path

    def _headers(self):
        if not self.pf:
            raise RuntimeError("还没设云端平台：右上角设置 → 云端 API，按接口格式新建一个平台并填 key")
        k = self.key()
        if not k:
            raise RuntimeError("还没填 API key：右上角设置 → 云端 API，填上后点「保存连接设置」")
        return {"Authorization": f"Bearer {k}"}

    @staticmethod
    def _check(r, what):
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message") or r.text
            except Exception:
                msg = r.text
            raise RuntimeError(f"{what}失败（HTTP {r.status_code}）：{msg[:300]}")
        try:
            return r.json()
        except ValueError:                               # 200 但不是 JSON（代理 / 登录页之类）
            raise RuntimeError(f"{what}：平台返回的不是 JSON：{r.text[:300]}")

    def check(self):
        """连通性检查：列一下模型（不花钱）。"""
        r = requests.get(self._url("/models"), headers=self._headers(), timeout=20)
        data = self._check(r, "连接")
        ids = [m.get("id") for m in data.get("data", [])]
        missing = [m for m in (self.cfg["image_model"],) if ids and m not in ids]
        return dict(ok=True, models=len(ids), missing=missing)

    IMAGE_WORDS = ("image", "dall-e", "seedream", "seededit", "flux", "kontext")    # 模型名里有这些的算图片模型

    @property
    def pf(self):
        return profile(self.cfg["provider"])

    def image_models(self):
        """平台上的图片模型名（GET {base_url}/models，按名字筛；配置里有 models_query 时按平台自己的分类取、不再按名字筛）。
        平台不给列表就返回 []（界面上还可以手填）。"""
        q = (self.pf or {}).get("models_query")
        r = requests.get(self._url("/models"), headers=self._headers(), params=q or None, timeout=20)
        if r.status_code >= 400:
            return []
        try:
            ids = [m.get("id") for m in r.json().get("data", []) if m.get("id")]
        except ValueError:
            return []
        return sorted({i for i in ids if q or any(w in i.lower() for w in self.IMAGE_WORDS)})

    def edit_image(self, image_path, prompt, work, seed=42, log=print, out_name="qwen_edit.png", refs=(), mask=None,
                   alpha=False, pad=True, ref_max=None):
        """改图。OpenAI 的输出只有几种固定尺寸：先把输入往外镜像补边到最接近的比例，出来再裁回、缩回原尺寸，
        不拉伸画面。seed 接口不支持，同一提示词多出几张靠模型本身的随机性。mask 不支持（整张重绘，调用方按遮罩拼回）。
        alpha 不支持：透明通道丢掉，只发 RGB。pad / ref_max 只对 ComfyUI 有意义，这里忽略。"""
        src = np.array(Image.open(image_path).convert("RGB"))
        h, w = src.shape[:2]
        if self.cfg["image_size"] == "match":
            W, H = min(EDIT_SIZES, key=lambda s: abs(np.log((s[0] / s[1]) / (w / h))))
            if w / h > W / H:                            # 原图更宽：上下补
                ph = round(w * H / W) - h
                padded = cv2.copyMakeBorder(src, ph // 2, ph - ph // 2, 0, 0, cv2.BORDER_REFLECT)
                crop = (ph // 2, ph // 2 + h, 0, w)
            else:                                        # 原图更高：左右补
                pw = round(h * W / H) - w
                padded = cv2.copyMakeBorder(src, 0, 0, pw // 2, pw - pw // 2, cv2.BORDER_REFLECT)
                crop = (0, h, pw // 2, pw // 2 + w)
            size = f"{W}x{H}"
        else:
            padded, crop, size = src, (0, h, 0, w), self.cfg["image_size"]
        buf = io.BytesIO()
        Image.fromarray(padded).save(buf, "PNG")
        log(f"  API 改图（{self.cfg['image_model']}，{size}{f'，另加 {len(refs)} 张参考图' if refs else ''}）…")
        if refs:                                         # 多张图：OpenAI 用 image[] 字段，顺序 = Picture 1、2…
            files = [("image[]", ("input.png", buf.getvalue(), "image/png"))] + \
                    [("image[]", (Path(r).name, Path(r).read_bytes(), "image/png")) for r in refs]
        else:
            files = {"image": ("input.png", buf.getvalue(), "image/png")}
        r = requests.post(self._url("/images/edits"), headers=self._headers(), timeout=600,
                          data=dict(model=self.cfg["image_model"], prompt=prompt, size=size, n="1"), files=files)
        j = self._check(r, "API 改图")
        item = dig(j, "data.0")
        if not isinstance(item, dict) or not (item.get("b64_json") or item.get("url")):
            raise RuntimeError(f"API 改图：返回值里没找到图片：{json.dumps(j, ensure_ascii=False)[:300]}")
        if item.get("b64_json"):
            raw = base64.b64decode(item["b64_json"])
        else:                                            # 有的兼容平台只返回网址
            raw = fetch(item["url"], "API 改图", 120)
        q = np.array(Image.open(io.BytesIO(raw)).convert("RGB"))
        q = cv2.resize(q, (padded.shape[1], padded.shape[0]), interpolation=cv2.INTER_CUBIC)
        y0, y1, x0, x1 = crop
        out = Path(work) / out_name
        Image.fromarray(q[y0:y1, x0:x1]).save(out)
        return out


class JsonSync(OpenAICompat):
    """JSON 同步格式（火山方舟等）：POST {base_url}{path}，第 1 张图 + 参考图放 image_field（base64 data URI），直接返回图。
    尺寸、额外字段、结果位置都按平台配置（CUSTOM）。seed、遮罩不发：多出几张靠模型自己的随机性，遮罩由调用方贴回。"""

    @property
    def name(self):
        return "api-" + self.cfg["provider"]

    def check(self):
        """不一定有不花钱的测试接口：试一下 /models，不行就只报 key 找到了。"""
        h = self._headers()
        try:
            r = requests.get(self._url("/models"), headers=h, timeout=20)
        except requests.RequestException as e:
            raise RuntimeError(f"连不上 {self.cfg['base_url']}：{e}")
        if r.status_code in (401, 403):
            self._check(r, "连接")
        if r.status_code < 400:
            try:
                return dict(ok=True, models=len(r.json().get("data", [])), missing=[])
            except Exception:
                pass
        return dict(ok=True, models=0, missing=[], note="已找到 API key；这个平台没有不花钱的测试接口，出一张图才知道模型名对不对")

    def _size(self, w, h):
        """火山方舟：宽x高，总像素不在这个模型的范围里就等比放大 / 缩小。"""
        m = self.cfg["image_model"].lower()
        lo, hi = next(((a, b) for k, a, b in ARK_PIXELS if k in m), ARK_PIXELS_DEFAULT)
        s = 1.0
        if w * h < lo:
            s = (lo / (w * h)) ** 0.5 * 1.001
        elif w * h > hi:
            s = (hi / (w * h)) ** 0.5 * 0.999
        return max(16, round(w * s)), max(16, round(h * s))

    def size_value(self, w, h):
        mode = self.pf.get("size", "none")
        if self.cfg.get("image_size") not in (None, "", "match", "auto"):
            return self.cfg["image_size"]
        if mode == "wxh_ark":
            return "%dx%d" % self._size(w, h)
        if mode == "wxh":
            return f"{w}x{h}"
        if mode == "ratio":
            return min(RATIOS, key=lambda r: abs(np.log((int(r.split(":")[0]) / int(r.split(":")[1])) / (w / h))))
        return None

    @staticmethod
    def _data_uri(path):
        im = Image.open(path).convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    def body(self, image_path, prompt, refs, log):
        pf = self.pf
        w, h = Image.open(image_path).size
        refs = list(refs)
        cap = int(pf.get("max_images") or 0)
        if cap and 1 + len(refs) > cap:
            log(f"  注意：这个平台最多收 {cap} 张图，后面 {1 + len(refs) - cap} 张参考图没发")
            refs = refs[:cap - 1]
        images = [self._data_uri(image_path)] + [self._data_uri(r) for r in refs]
        body = {"model": self.cfg["image_model"], "prompt": prompt,
                pf["image_field"]: images[0] if len(images) == 1 and pf.get("single_as_string") else images}
        size = self.size_value(w, h)
        if size:
            body["size"] = size
        body.update(pf.get("extra") or {})
        log(f"  API 改图（{pf.get('name', self.cfg['provider'])} · {self.cfg['image_model']}，{size or '尺寸交给平台'}"
            f"{f'，另加 {len(refs)} 张参考图' if refs else ''}）…")
        return body, (w, h)

    def save_result(self, item, wh, work, out_name):
        """item：含 b64_json / url 的对象，或者直接是网址 / 网址列表。存成原图大小。"""
        if isinstance(item, list):
            item = item[0] if item else None
        if isinstance(item, dict) and item.get("error"):
            raise RuntimeError(f"API 改图失败：{str(item['error'].get('message') or item['error'])[:300]}")
        if isinstance(item, dict) and item.get("b64_json"):
            raw = base64.b64decode(item["b64_json"])
        else:
            url = item.get("url") if isinstance(item, dict) else item
            if isinstance(url, list):
                url = url[0] if url else None
            if not url:
                raise RuntimeError("API 改图：返回值里没找到图片（平台配置里的 result 位置可能不对）")
            raw = fetch(url, "API 改图", 180)
        q = Image.open(io.BytesIO(raw)).convert("RGB")
        if q.size != wh:
            q = q.resize(wh, Image.LANCZOS)
        out = Path(work) / out_name
        q.save(out)
        return out

    def edit_image(self, image_path, prompt, work, seed=42, log=print, out_name="qwen_edit.png", refs=(), mask=None,
                   alpha=False, pad=True, ref_max=None):
        """改图。seed / mask / alpha / pad / ref_max 这个格式都不发（mask 由调用方按遮罩贴回）。"""
        body, wh = self.body(image_path, prompt, refs, log)
        r = requests.post(self._url(self.pf["path"]), headers=self._headers(), json=body, timeout=600)
        return self.save_result(dig(self._check(r, "API 改图"), self.pf["result"]), wh, work, out_name)


class AsyncTask(JsonSync):
    """异步任务格式（apimart 等）：提交 → 拿任务号 → 每隔几秒查一次 → 完成后下载图片。"""
    POLL_EVERY = 3
    POLL_MAX = 900                           # 最多等 15 分钟

    def edit_image(self, image_path, prompt, work, seed=42, log=print, out_name="qwen_edit.png", refs=(), mask=None,
                   alpha=False, pad=True, ref_max=None):
        pf = self.pf
        body, wh = self.body(image_path, prompt, refs, log)
        j = self._run(pf["path"], body, "改图", log)
        return self.save_result(dig(j, pf["result"]), wh, work, out_name)

    def _run(self, path, body, what, log):
        """提交 → 拿任务号 → 轮询到完成，返回完成时的查询结果（失败 / 超时抛错，带平台给的原因）。"""
        import time
        from engine import check_cancel
        pf = self.pf
        r = requests.post(self._url(path), headers=self._headers(), json=body, timeout=120)
        sub = self._check(r, "API 提交")
        tid = dig(sub, pf["task_id"])
        if not tid:
            raise RuntimeError(f"API 提交后没拿到任务号（平台配置里的 task_id 位置可能不对）：{json.dumps(sub, ensure_ascii=False)[:300]}")
        log(f"  已提交，任务号 {tid}，等平台{what}…")
        t0, last, errs, blank = time.time(), None, 0, 0
        while time.time() - t0 < self.POLL_MAX:
            check_cancel()
            time.sleep(self.POLL_EVERY)
            try:                                         # 网络偶尔断一下不算失败（平台那边任务还在跑、在计费）：连错 5 次才放弃
                r = requests.get(self._url(pf["poll"].replace("{id}", str(tid))), headers=self._headers(), timeout=60)
                errs = 0
            except requests.RequestException as e:
                errs += 1
                if errs >= 5:
                    raise RuntimeError(f"API 查询连续 {errs} 次连不上（任务号 {tid}，平台那边可能还在跑）：{e}")
                log(f"  查询没连上（{e.__class__.__name__}），稍后再试…")
                continue
            j = self._check(r, "API 查询")
            st = str(dig(j, pf["status"]) or "")
            blank = 0 if st else blank + 1
            if blank >= 5:                               # 一直拿不到状态：多半是配置里的 status 位置写错了，不要干等 15 分钟
                raise RuntimeError(f"API 查询的返回值里一直没有状态（平台配置里的 status 位置「{pf['status']}」可能不对）："
                                   f"{json.dumps(j, ensure_ascii=False)[:300]}")
            if st != last:
                log(f"  平台状态：{st or '（没有）'}（已用 {int(time.time() - t0)} 秒）")
                last = st
            if st in (pf.get("done") or ["completed"]):
                return j
            if st in (pf.get("failed") or ["failed"]):
                raise RuntimeError(f"API {what}失败：{str(dig(j, pf.get('error')) or st)[:300]}")
        raise RuntimeError(f"API {what}超时：等了 {self.POLL_MAX // 60} 分钟平台还没完成（任务号 {tid}）")

    # ---------- 视频生成（平台配置里有 video_path 才支持）----------
    @property
    def video_model(self):
        return self.cfg.get("video_model") or (self.pf or {}).get("video_model") or ""

    @property
    def video_resolution(self):
        opts = (self.pf or {}).get("video_resolutions") or []
        v = self.cfg.get("video_resolution")
        return v if v in opts else (opts[0] if opts else "")

    def upload(self, kind, path, mime):
        """素材先传到平台换网址（kind = video / image）。"""
        pf = self.pf
        with open(path, "rb") as f:
            r = requests.post(self._url(pf[f"upload_{kind}"]), headers=self._headers(), timeout=600,
                              files={"file": (Path(path).name, f, mime)})
        url = dig(self._check(r, "API 上传素材"), pf.get("upload_url") or "url")
        if not url:
            raise RuntimeError("API 上传素材后没拿到网址（平台配置里的 upload_url 位置可能不对）")
        return url

    def generate_video(self, video, images, prompt, out, log=print):
        """视频生成：video = 粗剪（调用方已按 video_limits 准备好），images = 参考图（顺序 = 提示词里的 @图片1、2…），
        prompt 原样发。平台出的视频下载到 out。"""
        pf = self.pf
        if not pf.get("video_path"):
            raise RuntimeError(f"「{pf.get('name', self.cfg['provider'])}」这个平台的配置里没有 video_path，不能做视频生成")
        cap = int(pf.get("video_max_images") or 0)
        images = list(images)
        if cap and len(images) > cap:
            log(f"  注意：这个平台最多收 {cap} 张参考图，后面 {len(images) - cap} 张没发")
            images = images[:cap]
        log(f"  上传粗剪视频{f'和 {len(images)} 张参考图' if images else ''}到平台…")
        body = {"model": self.video_model, "prompt": prompt, pf["video_field"]: [self.upload("video", video, "video/mp4")]}
        if images:
            body[pf["video_image_field"]] = [self.upload("image", f, "image/png") for f in images]
        if self.video_resolution:
            body["resolution"] = self.video_resolution
        body.update(pf.get("video_extra") or {})
        log(f"  API 视频生成（{pf.get('name', self.cfg['provider'])} · {self.video_model}，{self.video_resolution or '分辨率交给平台'}）…")
        j = self._run(pf["video_path"], body, "生成视频", log)
        url = dig(j, pf["video_result"])
        if isinstance(url, list):
            url = url[0] if url else None
        if not url:
            raise RuntimeError("API 视频生成：返回值里没找到视频（平台配置里的 video_result 位置可能不对）")
        cost = dig(j, "data.cost")
        Path(out).write_bytes(fetch(url, "API 视频生成", 600))
        log(f"  平台出的视频已下载{f'（平台记的费用 {cost}）' if cost is not None else ''}")
        return Path(out)


def make_api(cfg):
    fmt = (profile((cfg or {}).get("provider")) or {}).get("format")
    return {"json_sync": JsonSync, "async_task": AsyncTask}.get(fmt, OpenAICompat)(cfg)


class WithApi:
    """ComfyUI 引擎 + 云端平台（.api）：图片生成 / 视频生成面板切到 API 时用 .api，其余一律转给 ComfyUI 引擎。"""

    def __init__(self, base, cfg):
        self.base = base
        self.configure(cfg)

    def configure(self, cfg):
        self.cfg = {**DEFAULTS, **{k: v for k, v in (cfg or {}).items() if k in DEFAULTS}}
        self.api = make_api(self.cfg)

    def __getattr__(self, name):
        return getattr(self.base, name)
