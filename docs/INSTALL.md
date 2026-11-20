# ComfyUI Installation and Deployment Guide

English · [中文](安装.md)

Scope: the ReFrameVideo browser frontend + Python backend + ComfyUI inference, covering segmentation, reference-image editing and video generation, plus the optional cloud image / video APIs. Training environments are not covered.

## 1. The three components

```text
Browser → ReFrameVideo backend (default 8765, also serves the web/ frontend) → ComfyUI (usually 8188)
```

The frontend provides the canvas and editing tools; the backend handles projects, videos, masks and workflows; ComfyUI loads the models and runs inference. Models live on the machine that runs ComfyUI; source videos must be readable by the machine that runs the backend. The two can be different machines.

This repository contains the frontend (`web/`), the backend (`app/`) and the custom ComfyUI nodes (`comfyui/ComfyUI-Reframe/`). The project LoRA is on [Hugging Face](https://huggingface.co/VoidEclipseAI/ReFrameVideo-LoRA) and [ModelScope](https://modelscope.cn/models/VoidEclipseAI/ReFrameVideo-LoRA) (faster in mainland China).

## 2. Environment and layout

Environments actually tested:

| Component | Tested versions / setup |
|---|---|
| ReFrameVideo backend | Windows 10, Python 3.10 and 3.13 |
| ComfyUI | 0.39.0 and 0.45.5 (SAM, H3 video and Qwen Image 2.1 all work); MiniMax H3 Image editing and H3 Image PE need 0.45.0 or later (on 0.45.5 only the nodes were confirmed to load; image editing has not been run end to end) |
| GPU | ComfyUI ran on a separate RTX 4090 (24 GB); the machine running the backend needs no GPU |
| Linux / macOS | The code is written to be cross-platform but has never been run there |

Minimum VRAM, RAM and disk space have not been measured systematically, so a 4090 is not a hard requirement. The H3 main model and text encoder are large, and the disk also has to hold videos, previews, masks and intermediate generation files. Start with a short clip to check your setup.

Repository layout:

```text
ReFrameVideo/
├─ start.py / start.bat       One-click start (backend + frontend on one port)
├─ requirements.txt           Backend Python dependencies
├─ LICENSE                    License (AGPL-3.0)
├─ README.md / README_zh.md   Project overview (English / Chinese)
├─ app/                       Python backend (includes prompts/, prompt_format.py)
├─ web/                       Browser frontend (no build step)
├─ comfyui/ComfyUI-Reframe/   Custom nodes to install into ComfyUI
├─ workflows/                 Example H3 workflow (API format); image/ and video/ hold your own workflows (see the README in each)
├─ docs/                      Documentation
├─ app_settings.json          Settings created at runtime (not in git)
└─ projects/                  Project data (default location, not in git)
```

Do not copy `server.py` on its own: it depends on the other modules and prompt files in `app/`, and the frontend is in `web/`.

## 3. Install ComfyUI

On Windows, follow the [official ComfyUI portable guide](https://docs.comfy.org/installation/comfyui_portable_windows): download the package that matches your GPU, extract it and run `run_nvidia_gpu.bat`. Below, `ComfyUI/` means the folder that contains `main.py`, `models/` and `custom_nodes/` (in the portable package: `ComfyUI_windows_portable\ComfyUI\`).

Make sure ComfyUI opens in a browser before installing models and nodes. On a single machine use `http://127.0.0.1:8188`. Across machines, set the listen address and firewall as described in the official guide and allow only trusted networks; pass the ComfyUI machine's address to ReFrameVideo with `--comfy`.

ComfyUI ships its own Python environment; the ReFrameVideo backend's virtual environment below is a separate one. Install custom-node dependencies into ComfyUI's Python.

## 4. Model files and where to put them

The file names below come from the current backend (`app/engine.py`) and are the names the program actually uses. Do not rename files of a different architecture or quantization to match them. If your file names differ, use the model selectors in the UI or adjust the backend configuration; not every file can be swapped in the UI.

| Purpose | Model file | Folder (relative to ComfyUI) | When it is needed |
|---|---|---|---|
| Object detection and tracking | `sam3.1_multiplex_fp16.safetensors` | `models/checkpoints/` | SAM segmentation |
| Video generation main model | `minimax_h3_ref2va_int8_convrot.safetensors` | `models/diffusion_models/` | H3 video generation |
| H3 text encoder | `qwen3vl_32b_minimax_h3_int8_convrot.safetensors` | `models/text_encoders/` | H3 video generation |
| H3 video VAE | `minimax_h3_video_vae_fp16.safetensors` | `models/vae/` | H3 video generation |
| H3 audio VAE | `minimax_h3_audio_vae_fp32.safetensors` | `models/vae/` | The workflow still loads it, so it must be present |
| Image editing, Turbo | `qwen_image_2.1_turbo_bf16.safetensors` | `models/diffusion_models/` | Qwen image editing; one of three versions, as selected |
| Image editing, full | `qwen_image_2.1_bf16.safetensors` | `models/diffusion_models/` | Same as above |
| Image editing, int8 | `qwen_image_2.1_int8_convrot.safetensors` | `models/diffusion_models/` | Same as above |
| Qwen text / vision encoder | `qwen3vl_8b_int8_convrot.safetensors` | `models/text_encoders/` | Qwen image editing and image description |
| Qwen image VAE | `qwen_image_2.1_vae_bf16.safetensors` | `models/vae/` | Qwen image editing |
| Edit prompt enhancer | `qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors` | `models/text_encoders/` | When PE is set to "Qwen 2.1 PE" |
| MiniMax H3 Image editing | `minimax_h3_image_pruned_int8_convrot.safetensors` | `models/diffusion_models/` | When the image model is "MiniMax H3 Image" (shares the `qwen3vl_32b_minimax_h3_int8_convrot` text encoder with H3 video) |
| H3 Image VAE | `minimax_h3_image_vae_fp16.safetensors` | `models/vae/` | Same as above |
| H3 Image prompt enhancer | `qwen3.5_9b_w4a8_gptq.safetensors` | `models/text_encoders/` | When PE is set to "H3 Image PE" |
| ReFrameVideo LoRA | `ReFrameVideo-H3-LoRA-v1.safetensors` ([Hugging Face](https://huggingface.co/VoidEclipseAI/ReFrameVideo-LoRA) / [ModelScope](https://modelscope.cn/models/VoidEclipseAI/ReFrameVideo-LoRA)) | `models/loras/` | Select it under LoRA for video generation, strength 1.0; use 6 steps together with the Pika H3 Flex speed-up LoRA (`pika_h3_flex_ref2va_v1_bf16.safetensors`, 1.0, in `loras/` of [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3)). The code runs without it, but results are much worse (H3 often copies the gray as is) |
| Soft-edge matting | `matanyone2.pth` | `custom_nodes/ComfyUI-MatAnyone/checkpoint/` | Optional MatAnyone2 node |

Model sources:

- H3: [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/tree/main); download each file from the repo's `diffusion_models`, `text_encoders` and `vae` folders.
- H3 Image: [Comfy-Org/MiniMax-H3-Image](https://huggingface.co/Comfy-Org/MiniMax-H3-Image/tree/main) (model, VAE); the PE is in [Comfy-Org/Qwen3.5](https://huggingface.co/Comfy-Org/Qwen3.5/tree/main/text_encoders). Requires ComfyUI 0.45.0 or later. Read the original license before use (see the [MiniMax-H3-Image model repo](https://huggingface.co/Comfy-Org/MiniMax-H3-Image/tree/main)).
- SAM: [Comfy-Org/sam3.1 checkpoints](https://huggingface.co/Comfy-Org/sam3.1/tree/main/checkpoints).
- Qwen: follow the model download links in the [official ComfyUI Qwen Image 2.1 tutorial](https://github.com/Comfy-Org/docs/blob/main/tutorials/image/qwen/qwen-image-2-1.mdx) and check the file names against the table above. Do not download the older Qwen Image Edit by mistake; the current code uses nodes specific to 2.1. Not every Qwen download link has been checked individually.
- MatAnyone2: get the model from the download link in the [ComfyUI-MatAnyone author's instructions](https://github.com/FuouM/ComfyUI-MatAnyone) and put it in the node's `checkpoint/` folder.
- Project LoRA: [VoidEclipseAI/ReFrameVideo-LoRA](https://huggingface.co/VoidEclipseAI/ReFrameVideo-LoRA) (ModelScope mirror: [same name](https://modelscope.cn/models/VoidEclipseAI/ReFrameVideo-LoRA)); recommended recipe in the table above (ReFrameVideo LoRA 1.0 + Pika H3 Flex 1.0, 6 steps).

After downloading, restart ComfyUI and then refresh ReFrameVideo. The main models and SAM are chosen in their panels; companion files such as encoders, VAEs and PEs can be changed under **Model files** in the Image generation / Video generation panels (if a default file is missing, another quantization of the same model is used automatically). LoRAs must be selected by hand in the generation panel; putting them in the folder does not enable them.

## 5. Install nodes

### Required custom nodes

Copy the whole `comfyui/ComfyUI-Reframe/` folder from this repository to:

```text
ComfyUI/custom_nodes/ComfyUI-Reframe/
├─ __init__.py
├─ cleanup_route.py
├─ h3_inpaint_mask.py
├─ h3_mask.py
├─ io_nodes.py
└─ pyproject.toml
```

Files the app sends to ComfyUI go to `ComfyUI/input/reframe/`. When each workflow ends (success, error or stop) they are deleted through the node pack's `POST /reframe/input/delete`, which only deletes files starting with `videops_` inside `input/reframe/`. With an older node pack the files stay in `input/reframe/`; this does not affect generation, and you can delete them by hand.

Restart ComfyUI and check that the node `ReframeH3InpaintMask` exists, as well as the 6 input / output marker nodes under the Reframe/IO category (used by your own image / video workflows, see `workflows/image/README.md` and `workflows/video/README.md`). The pack uses the ComfyUI V3 API and has no extra third-party dependencies. Video generation checks for this node explicitly and reports an error if it is missing.

### Nodes ComfyUI itself must provide

| Purpose | Key nodes |
|---|---|
| SAM | `CheckpointLoaderSimple`, `SAM3_Detect`, `SAM3_VideoTrack` |
| H3 | `MiniMaxH3ReferenceToVideo`, `LTXVConcatAVLatent`, `LTXVSeparateAVLatent` |
| Qwen image editing | `QwenImage21Cache`, `TextEncodeQwenImage21` |
| Description / prompt enhancement | `TextGenerate`, `PreviewAny`, `BatchImagesNode` (for multiple images) |
| H3 Image editing / H3 Image PE (ComfyUI 0.45.0+) | `MiniMaxH3ImageConditioning`, `ConditioningZeroOut`; the PE uses `TextGenerateV2` |
| Basic loading and output | `UNETLoader`, `CLIPLoader`, `VAELoader`, `LoadVideo`, `GetVideoComponents`, `CreateVideo`, `SaveVideo` |

This table lists key capabilities, not every node in the workflows. For H3, `CLIPLoader` must also support `type=minimax`; for Qwen the type is `qwen_image`. If a node is missing, check the ComfyUI version and the startup log first; having the model files in place does not mean the environment is ready.

### Optional: MatAnyone2 soft-edge matting

Install the author's `FuouM/ComfyUI-MatAnyone` into `custom_nodes/ComfyUI-MatAnyone/` and install its dependencies as it describes. For the Windows portable build, run this from the outer folder:

```powershell
.\python_embeded\python.exe -m pip install -r .\ComfyUI\custom_nodes\ComfyUI-MatAnyone\requirements.txt
```

Then put `checkpoint/matanyone2.pth` in place and restart. The backend calls the API node `MatAnyone2` with parameters including `src_video`, `foreground_MASK` and `mask_frame`. Similar nodes from other authors are not interchangeable; go by the API name and fields registered in `/object_info`. Without it, edges use SAM's hard edges.

## 6. Install the ReFrameVideo backend dependencies

On Windows, double-clicking `start.bat` does the following automatically: it creates `.venv` if it does not exist, and reinstalls when `requirements.txt` differs from the last install (recorded in `.venv\requirements.installed.txt`). If the install breaks, delete the `.venv` folder and run it again.

To install by hand, use a separate virtual environment in the repository root `ReFrameVideo/`:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`websocket-client` is used to show ComfyUI sampling progress; without it only the stage and elapsed time are shown. The browser frontend needs no npm build; Node.js is only used for development checks.

You also need FFmpeg with its `bin` folder on PATH. The backend calls `ffmpeg` and `ffprobe` directly; installing a Python package of the same name is not enough:

```powershell
ffmpeg -version
ffprobe -version
.\.venv\Scripts\python.exe -c "import fastapi, uvicorn, numpy, cv2, PIL, requests, scipy, websocket; print('imports OK')"
```

**Linux / macOS** (the code is written to be cross-platform, but has only been run on Windows):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
sudo apt install ffmpeg python3-tk   # Debian / Ubuntu; on macOS: brew install ffmpeg python-tk
```

- `python3-tk` is only used for the file picker that opens with **Open video**; without it, or on a server without a desktop, type the video path into the box instead.
- `start.bat` is Windows only; on other systems run `.venv/bin/python start.py`.
- **Show in folder**: Windows / macOS select the file; Linux opens the containing folder with `xdg-open`.

## 7. Start and configure

Start ComfyUI first, then double-click `start.bat` (Linux / macOS: `.venv/bin/python start.py`). On first start the window asks for the ComfyUI address: press Enter for `http://127.0.0.1:8188`, or type `mock` for the mock engine (UI only, no detection or generation). If the address cannot be reached you are told so and can enter it again or use it anyway. The address is saved as `comfy_url` in `app_settings.json` and is not asked again. To change it:

```powershell
.\start.bat --comfy http://192.168.1.10:8188
```

Open `http://127.0.0.1:8765/` in a browser. If ReFrameVideo is already running on 8765, that instance is reused (the new address is saved, but the running instance does not switch; close it first to switch).

Other options: `--port 8766` (another port), `--projects D:\my_projects` (another projects folder; its parent folder must exist), `--no-browser`. The backend also reads the `VE_PROJECTS`, `VE_COMFY` and `VE_PORT` environment variables. To start the backend directly: `python app/server.py --port 8765 --comfy http://127.0.0.1:8188` (without `--comfy` it uses the mock engine, which the tests rely on).

In Settings, make sure image editing and description use ComfyUI. Then choose the SAM model, the Qwen version, the H3 main model and the LoRAs you need. The built-in Qwen recipes are Turbo 8 steps and full / int8 25 steps; for H3 the recommendation is ReFrameVideo LoRA 1.0 + Pika H3 Flex 1.0, 6 steps. **Working resolution for imported videos** (Settings → Performance) defaults to a long edge of 1920: larger videos are scaled down on import before processing.

## 8. Step-by-step check after installing

1. Open ComfyUI in a browser; visit its `/object_info` and check that the key nodes and model loader lists are there.
2. Open ReFrameVideo's `/api/info` and check that it is the real engine, not mock. Passing the connection check in Settings only means the service endpoint responds.
3. Import a short video, check that it decodes and plays, and test SAM detection and tracking.
4. Move an object, generate a first-frame reference image and use it, to check the Qwen path.
5. Check the rough cut, redraw area, reference images and LoRA, then generate a short clip to check the H3 path.
6. Check the start, middle, end and audio track, then test export. For the full walkthrough see the [tutorial](../web/ui/tutorial-en.md).

Workflows are built by the backend and submitted through the ComfyUI API; in normal use you do not build graphs by hand on the ComfyUI canvas. For a failed job, `comfy_graph.json` shows exactly what was submitted.

## 9. Where settings, projects and results are stored

`app_settings.json` in the repository root stores model choices, LoRAs, sampling parameters, service choices and so on; theme, language and some layout settings are stored in the browser. Copying the page files alone is not a backup.

Projects are in `projects/<pid>/` in the repository by default (change it with `start.py --projects`). Projects in the old layout are migrated to the layout below the first time they are opened. Folder names are bilingual (Chinese + English):

```text
project.json                     Project data (layers, versions, reference images, result list…)
原视频.mp4                       Original video as imported (scaled to the working resolution)
版本version/vN/视频video/        Video and preview frames for each version
结果result/vN/视频video/         Video results: <project name>_vN_K.mp4
结果result/vN/图片images/        Image candidates and the chosen one (hard links, no extra space)
中间文件work/生成gen/NNN/        Per-generation rough cut, mask, workflow and the input list inputused.txt
中间文件work/图片images/<id>/    Base images and candidates for image editing
背景图bg/  参考图refs/  masks/  缓存cache/ (can be cleared in Settings)  导出export/
```

Keep the whole project folder; moving or deleting files inside it on their own can break references. ComfyUI also keeps uploaded inputs and outputs, so disk use does not come from ReFrameVideo alone.

`app_settings.json`, `api_keys.local.json` and `projects/` are in `.gitignore` and are never committed.

### Security notes

- The backend listens only on `127.0.0.1` and only accepts requests from local pages: requests whose `Host` is not a local name, and state-changing requests from other websites, are rejected (403). Do not expose it to the internet through a reverse proxy; it has no login or access control.
- API keys are stored in **plain text** in `api_keys.local.json` in the repository root. The backend never sends a key back to the browser (only whether one is set and its last few characters). Keep this file in mind on shared computers.

## 9.5 Cloud API (image generation / video generation)

Switch the top of the Image generation / Video generation panel to **API** to call the cloud provider you configured (only the input image / rough cut, reference images and your prompt are sent; results are not guaranteed). The cloud API is used only by the Image generation and Video generation panels; detection, matting, image description and H3 video always go through ComfyUI. Keys are stored locally in `api_keys.local.json` and are not in git.

No provider is built in. In Settings → Cloud API → **API config**, use **New from template** to pick a format, fill in the empty fields from the provider's API docs, click **Save as provider…**, then enter the key and click **Save connection settings**:

| format | How requests are sent | Example |
|---|---|---|
| `openai_edits` | `POST {base_url}/images/edits`, multipart image upload, synchronous `data[0].b64_json / url` | OpenAI image edit API |
| `json_sync` | `POST {base_url}{path}`, images in JSON fields (base64 data URIs), image returned directly | Volcengine Ark Seedream |
| `async_task` | Submit to get a task ID, poll `GET {base_url}{poll}`, then download the image URL; the only format that can do video generation | APIMart |

Config fields (`json_sync` / `async_task`):

| Field | Meaning |
|---|---|
| `base_url` | Provider address, e.g. `https://api.example.com/v1` |
| `image_model` | Default image model (required; can be changed in the panel) |
| `path` | Submit path, e.g. `/images/generations` |
| `image_field` | Field that holds the images (`image`, `image_urls`…); the first image is the one to edit, the rest are references |
| `single_as_string` | Send a string when there is only one image (otherwise always an array) |
| `size` | `wxh_ark` (width x height, scaled to each Ark model's pixel range) / `wxh` (original width x height) / `ratio` (closest ratio, e.g. `16:9`) / `none` (not sent) |
| `extra` | Extra fields always sent, e.g. `{"watermark": false}`, `{"resolution": "2K"}` |
| `max_images` | Maximum number of images (original + references); extra references are dropped; 0 = no limit |
| `result` | Where the result is in the response, as a dotted path where numbers are indexes. It can point to an object with `b64_json` / `url`, or directly to a URL |
| `task_id` | (async) Where the task ID is, e.g. `data.0.task_id` |
| `poll` | (async) Status path, `{id}` is replaced by the task ID, e.g. `/tasks/{id}` |
| `status` / `done` / `failed` | (async) Where the status is, and the values meaning done and failed |
| `error` | (async) Where the failure reason is |
| `models_query` | Parameters sent when listing models (`GET {base_url}/models`), e.g. `{"category": "image"}` |

### Config examples

Volcengine Ark Seedream (`json_sync`, not tested with a real key):

```json
{"format": "json_sync", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "image_model": "doubao-seedream-5-5-261028",
 "path": "/images/generations", "image_field": "image", "single_as_string": true, "size": "wxh_ark",
 "extra": {"response_format": "b64_json", "watermark": false}, "result": "data.0", "max_images": 10}
```

APIMart (`async_task`, image + Seedance video, tested by the author):

```json
{"format": "async_task", "base_url": "https://api.apimart.ai/v1", "image_model": "seedream-4.5",
 "path": "/images/generations", "image_field": "image_urls", "size": "ratio", "extra": {},
 "task_id": "data.0.task_id", "poll": "/tasks/{id}", "status": "data.status", "done": ["completed"], "failed": ["failed"],
 "error": "data.error.message", "result": "data.result.images.0.url.0", "models_query": {"category": "image"}, "max_images": 3,
 "video_model": "seedance-2-5-flash", "video_path": "/videos/generations", "video_field": "video_urls", "video_image_field": "image_urls",
 "upload_video": "/uploads/videos?retention_days=3", "upload_image": "/uploads/images?retention_days=3", "upload_url": "url",
 "video_result": "data.result.videos.0.url.0",
 "video_extra": {"omni_reference_task_type": "edit", "size": "adaptive", "generate_audio": false, "watermark": false},
 "video_resolutions": ["480p", "720p"], "video_max_images": 9,
 "video_limits": {"min_s": 4, "max_s": 30, "min_ratio": 0.4, "max_ratio": 2.5, "min_pixels": 409600, "min_side": 300, "min_fps": 24, "max_mb": 50}}
```

### Video generation through the API

The top of the Video generation panel can also be switched to **API** (a warning appears when you switch: in the author's tests Seedance cannot yet fill in the video reliably). Only three things are sent: the rough cut (empty areas painted gray 128; `@视频1` in the prompt), the reference images checked in the panel (`@图片1`, `@图片2`…) and your prompt (sent as is). ComfyUI is not called: no H3 and no Qwen-VL. The provider's video is the final result, with the frame count matched to the original and the original audio.

`@视频1` and `@图片N` are Seedance's own reference syntax and stay the same in the English UI.

Only `async_task` providers with `video_path` in their config are supported (the author tested APIMart · `seedance-2-5-flash`). Video fields:

| Field | Meaning |
|---|---|
| `video_model` | Default video model (can be changed in the panel) |
| `video_path` | Video submit path, e.g. `/videos/generations` |
| `video_field` / `video_image_field` | Fields for the rough-cut video URL / reference image URLs (`video_urls` / `image_urls`) |
| `upload_video` / `upload_image` | Paths to upload files and get URLs; `upload_url` = where the URL is in the upload response |
| `video_result` | Where the result video URL is, e.g. `data.result.videos.0.url.0` |
| `video_extra` | Extra fields always sent, e.g. `{"omni_reference_task_type": "edit", "size": "adaptive"}` |
| `video_resolutions` | Available resolutions (sent in the `resolution` field) |
| `video_max_images` | Maximum number of reference images |
| `video_limits` | The provider's limits on the input video, checked locally before sending: `min_s` / `max_s` duration, `min_ratio` / `max_ratio` aspect ratio (an error is reported and nothing is uploaded if they fail); `min_pixels` / `min_side` (scaled up if too small), `min_fps` (frames interpolated if too low, duration unchanged), `max_mb` upload size |

Notes:
- In the author's tests, Seedance could not yet reliably fill gray empty areas: it may leave the gray in place or draw extra people in the gaps. Check results carefully.
- Seedance may reject film clips; for footage with real human faces, the provider may require the material to pass a face review first.
- Usage is billed by the provider at its own prices.

For providers that fit none of the three formats, build a workflow in ComfyUI with that provider's nodes, put it in `workflows/image/` (see the README there) and select it in ComfyUI mode.

## 10. Troubleshooting

| Symptom | Check first |
|---|---|
| The page opens but uses the mock engine | Whether `comfy_url` is set in `app_settings.json` (`start.py --comfy`); whether an old instance was reused |
| Model dropdowns are empty | Whether the backend can reach ComfyUI; whether the files are in the right model folder on the right machine |
| File exists but fails to load | File name, quantization, matching encoder / VAE, ComfyUI support, and whether the download is complete |
| ReframeH3InpaintMask not found | Whether the custom node folder is at the right level; whether ComfyUI was restarted; import errors in the startup log |
| Qwen main model present but still fails | Whether the two companion files and the Qwen 2.1 nodes are all there |
| Prompt enhancement fails | Whether the PE encoder is on ComfyUI; with H3 Image PE, whether ComfyUI is 0.45.0 or later and the task prompt was downloaded (see "H3 Image PE prompt" below) |
| MiniMax H3 Image is grayed out | ComfyUI version (needs 0.45.0+); whether `minimax_h3_image_pruned_int8_convrot.safetensors` and `minimax_h3_image_vae_fp16.safetensors` are in place |
| MatAnyone2 fails | The node's API name and fields, its own checkpoint folder and its Python dependencies |
| FFmpeg not found | Run `ffmpeg -version` in the same terminal that starts the backend; reopen the terminal after updating PATH |
| Out of VRAM | Use a shorter test clip, lower the working resolution, check the ComfyUI log; do not assume low VRAM will work |
| Reference image had no effect | Whether it was chosen and checked; whether it was skipped because the layout changed or the size did not match |
| Backend version too old | Frontend and backend must come from the same repository version; restart the backend |

### H3 Image PE prompt

"H3 Image PE" uses MiniMax's official task prompt. Its license is the MiniMax-H3-Image Community License, so it is not included in this repository: on first use the backend downloads it from Hugging Face, checks it against the sha256 in the official manifest and saves it to `app/prompts/third_party/h3img_pe_edit_task_prompt.txt` (not in git).

If the download fails (for example, Hugging Face cannot be reached), download it by hand and put it at the path above:

- URL: `https://huggingface.co/MiniMaxAI/MiniMax-H3-Image-PE/resolve/main/prompt/edit/task_prompt.txt`
- sha256: `c5695dbfc07a9e65555cef67d92467c613371cc2039b96f057022246a30a57b2`

## 11. Not finished / not verified

- H3 Image PE, Volcengine Ark Seedream with a real key, Linux / macOS: the code is written but has not been fully run on real hardware.
- No pinned dependency versions and no record of hardware usage.

If you run into problems, please open an Issue with your environment, steps to reproduce and the job log.
