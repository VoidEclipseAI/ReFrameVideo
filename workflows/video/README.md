# 自己的视频工作流

把 ComfyUI 里搭好的视频工作流放进这个文件夹，软件的「视频生成 → 模型」下拉里就会多出「工作流 · 文件名」。
选了它，点「生成视频」时软件照常做粗剪、重绘 mask、参考图和提示词，然后交给你的工作流跑，成片放进「视频结果」。
这个文件夹里的 `.json` 不进 git。

## 怎么做

1. ComfyUI 那台机器的 `custom_nodes/` 里要有本仓库的 `comfyui/ComfyUI-Reframe/`（节点在 Reframe/IO 分类下），装好重启 ComfyUI。
2. 在 ComfyUI 里搭工作流，用这几个节点标出入口和出口。它们只是直通，接上以后工作流在 ComfyUI 里照常能跑：

| 节点 | 接法 | 软件运行时放进来的东西 |
|---|---|---|
| Reframe Video Input（name = rough_cut，必须有） | 任意帧（Load Video → Get Video Components）→ 它 → 原来用视频的地方 | 粗剪：按编辑摆好、空的地方是灰 128；宽高 = 面板「输出分辨率」算出来的大小，帧数 = 原片帧数 |
| Reframe Video Input（name = guide，可以没有） | 同上 | 粗剪缩到 768 宽（H3 当 `<Video 1>` 用的那份） |
| Reframe Mask Input（可以没有） | 任意 mask → 它 → 用 mask 的地方 | 每帧一张重绘 mask（白 = 要重画：空区 + 过渡带），和粗剪一样大、一样多帧 |
| Reframe Image Input（index = 1、2…，可以没有） | Load Image → 它 → 用参考图的地方，index 从 1 连续排 | 「视频生成」面板勾的参考图，顺序同面板（首帧、尾帧、背景图、资料卡、其他图片里勾了的） |
| Reframe Text Input（name = prompt） | 它 → 文本编码节点的 text | 软件写的完整提示词（H3 六段格式 + 空区分析）。有这个节点时才会问看图模型 |
| Reframe Text Input（name = user） | 同上 | 只有用户在「用户提示词」里写的原文 |
| Reframe Number Input（name = seed / width / height / frames） | 它 → 采样器的 seed，或宽 / 高 / 长度 | 面板的 Seed；粗剪的宽、高；原片帧数 |
| Reframe Output（正好一个） | 解码出来的逐帧图片 → 它（后面可以再接 Create Video / Preview） | 软件从这里取成片 |

3. 菜单「导出 (API)」（Export (API)）存成 `.json`，放进这个文件夹。普通的「保存」格式不行。
4. 回软件刷新页面，在「视频生成 → 模型」里选它。

## 注意

- 帧数是原片的帧数，软件不补。模型要求 4k+1、8k+1 之类时，在工作流里自己补帧；多出来的尾帧软件会截掉。
  输出帧数比原片少时，按时间比例拉回原片帧数。帧率按原片算，工作流不要改帧率。
- 宽高是 32 的倍数（和 H3 一样的算法）。输出是什么大小成片就是什么大小。
- 声音用原片的，工作流输出的声音不要。
- 工作流要几张参考图（Reframe Image Input 的个数），这次就得勾几张；勾多了后面的不发（日志里会写）。
- 选了工作流时，H3 的 LoRA、采样步数、二次采样都不用（面板上会藏掉）；分辨率、Seed、提示词、空区分析、参考图照用。
- 提示词里的「@名称」换成 `<Picture N>` / `<Video 1>`（H3 的写法）。
- 每次生成的输入和填好的工作流在项目的生成文件夹里（`wf_rough_cut.mp4`、`wf_mask.mov`、`comfy_graph.json`、`inputused.txt`）。
