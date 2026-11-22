# 在 ReFrameVideo 里编辑，导出到自己的 ComfyUI 运行

[English](tutorial-comfyui-en.md) · 中文 · [新手教程](tutorial-zh.md)

ReFrameVideo 负责编辑：识别主体、移动缩放、改画幅、画重绘范围、选首帧。真正跑模型的是 ComfyUI。
「导出」会把这次要交给模型的东西全部打包成 zip，包括预填好的粗剪、遮罩、参考图、提示词和工作流。拿到你自己的 ComfyUI 里就能直接跑，也可以自己改工作流。

适合这些情况：
- 想在别的电脑或云端 ComfyUI 上跑。
- 想自己改步数、seed、LoRA，或者接到自己的工作流里。
- 想看清楚软件到底给模型喂了什么。

## 1. 准备 ComfyUI

和软件直接生成用的是同一套东西，详见[安装指南](../../docs/安装.md)：
- 支持 MiniMax H3 的 ComfyUI（测过 0.45.5）。
- 节点包 `ComfyUI-Reframe`：把仓库里的 `comfyui/ComfyUI-Reframe/` 整个拷到 `ComfyUI/custom_nodes/`，再重启 ComfyUI。工作流里的 `ReframeH3InpaintMask` 节点来自这里。
- H3 视频模型、文本编码器、两个 VAE，以及 LoRA（ReFrameVideo LoRA；推荐再加 Pika 加速 LoRA，6 步）。

## 2. 在软件里编辑好

照[新手教程](tutorial-zh.md)第 3 节做到「准备首帧参考图」：识别、移动、改画幅、按 M 检查重绘范围、在「图片结果」里选用首帧。

然后在「视频生成」面板把要传的参考图勾好，提示词留空或自己写，再设好分辨率和 Seed。导出会照这个面板的设置准备素材。

## 3. 导出视频生成素材

1. 停在「视频编辑」页。
2. 右侧图标栏点「导出」，再点「导出视频生成素材」。
3. 这一步不调用 H3，但要准备粗剪，还要用看图模型逐块写空区描述（「空区分析」开着时约 1 分钟，会排队）。完成后浏览器自动下载 zip。
4. 同一个 zip 也存在项目文件夹的 `导出export/` 里，「在文件夹中显示」能找到。

zip 里有：

| 文件 | 是什么 |
|---|---|
| `rough_cut.mp4` | 粗剪：按你的编辑摆好，空的地方涂灰 128。已缩到 H3 画布大小，帧数补到 17k+5 |
| `mask.mov` | 重绘遮罩（白 = 重画）。png 编码，无损。**不要转成 mp4**：h264 会把 255 变成 254 |
| `video1_guide.mp4` | `<Video 1>` 参考视频（粗剪缩到 768 宽） |
| `picture_N_*.png` | 参考图，N 对应提示词里的 `<Picture N>`（如 `picture_1_first_frame.png`） |
| `partial_mask.mov` | 只有开了「主体重绘强度」才有 |
| `prompt.txt` | 提示词（Ref2VA 六段格式） |
| `workflow_api.json` | ComfyUI 工作流（API 格式），里面的文件名已经填成 zip 里的名字 |
| `说明.txt` | 上面这些的简短说明 |

## 4. 在 ComfyUI 里运行

1. 解压 zip，把视频和图片（`.mp4`、`.mov`、`.png`）拷到 `ComfyUI/input/`，直接放在这个文件夹里，不要放进子文件夹。
2. 把 `workflow_api.json` 拖进 ComfyUI 网页（或用菜单打开）。
3. 检查加载模型的节点：模型和 LoRA 的文件名是导出时软件设置里的。和你 ComfyUI 里的文件名不一样，就在节点里换成你的。
4. 点运行。成片在 `ComfyUI/output/videops/` 里。

工作流里常改的节点：

| 想改什么 | 节点 |
|---|---|
| Seed | `RandomNoise` |
| 步数 | `BasicScheduler`（用 Pika 加速 LoRA 时 6 步） |
| LoRA 和强度 | `LoraLoaderModelOnly` |
| 提示词 | `MiniMaxH3ReferenceToVideo` 的 prompt |
| 参考图 | `LoadImage`（换图时文件名要对上） |

## 5. 和在软件里直接生成有什么不同

软件在 H3 跑完后还会做几件事，自己在 ComfyUI 跑时没有：
- **帧数**：H3 要 17k+5 帧，粗剪在末尾补过帧。软件会只保留前面和原视频一样多的帧。自己跑出来的要按原视频帧数剪掉末尾。
- **声音**：软件把原视频的声音配回成片。
- **颜色校正**：软件按不重绘的地方把整体颜色对齐原视频（「颜色校正」）。
- **二次采样**：勾了「二次采样（高清）」时，软件会再跑一遍更高分辨率。导出的工作流只有第一遍。

## 6. 只导出改图素材（首帧 / 尾帧）

想用自己的图片模型做首帧：
1. 在「图片编辑」打开首帧 / 尾帧标签，写好提示词。
2. 「导出」→「导出改图素材」。
3. zip 里有 `input.png`（空处涂灰 128）、`mask.png`（白 = 空处）、参考图、`prompt.txt`，还有 Qwen Image 2.1 或 MiniMax H3 Image 的 `workflow_api.json`（按软件里选的模型）。用法同上：图片拷到 `ComfyUI/input/`，打开工作流运行。
4. 生成好的图可以传回软件：在「视频生成」面板的参考图列表里，首帧那一行点「换」→「上传一张」，上传的图直接当成选用的首帧。

## 7. 导出已经生成过的结果

在「视频结果」页 →「导出」→「打包这条结果和素材」：除了成片和原视频，还有当时交给 H3 的全部素材和工作流（和第 3 节一样）。可以拿来复现，或改个 seed 重跑。

仓库里也有一个现成的例子：[workflows/ReFrameVideo_H3_example_workflow_api.json](../../workflows/ReFrameVideo_H3_example_workflow_api.json)，是新手教程里那个例子成片用的工作流。
