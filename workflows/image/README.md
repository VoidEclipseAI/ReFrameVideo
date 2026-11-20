# 自己的改图工作流

把 ComfyUI 里搭好的改图工作流放进这个文件夹，软件的「图片生成 → 模型」下拉里就会多出「工作流 · 文件名」。
这个文件夹里的 `.json` 不进 git。

## 怎么做

1. ComfyUI 那台机器的 `custom_nodes/` 里要有本仓库的 `comfyui/ComfyUI-Reframe/`（节点在 Reframe/IO 分类下），装好重启 ComfyUI。
2. 在 ComfyUI 里搭工作流，用这几个节点标出入口和出口。它们只是直通，接上以后工作流在 ComfyUI 里照常能跑：

| 节点 | 接法 | 软件运行时放进来的东西 |
|---|---|---|
| Reframe Image Input（index = 1） | Load Image → 它 → 原来用这张图的地方 | 要改的图（空的地方是灰 128） |
| Reframe Image Input（index = 2、3…） | 同上，每张参考图一个，index 从 1 连续排 | 参考图，顺序 = 提示词里的 Picture 2、3… |
| Reframe Mask Input（可以没有） | 任意遮罩 → 它 → 用遮罩的地方 | 遮罩（白 = 重画）；整张重画时是全白 |
| Reframe Text Input | 它 → 文本编码节点的 text | 提示词 |
| Reframe Number Input（name = seed / width / height） | 它 → 采样器的 seed，或宽 / 高 | seed；第 1 张图的宽、高 |
| Reframe Output（正好一个） | 最终图片 → 它（后面可以再接 Save Image / Preview Image） | 软件从这里取结果 |

3. 菜单「导出 (API)」（Export (API)）存成 `.json`，放进这个文件夹。普通的「保存」格式不行。
4. 回软件刷新页面，在「图片生成 → 模型」里选它。

## 注意

- 工作流要几张图（Reframe Image Input 的个数），软件这次就得有几张：比如工作流有 index 2，但这次没有背景图 / 参考图，会报错。
  参考图比工作流要的多时，多出来的不发（日志里会写）。
- 输出和第 1 张图大小不一样时，软件会缩回第 1 张图的大小。
- 没有 Reframe Mask Input 时，「只填空处」「局部重画」由软件把结果按范围贴回。
- 提示词是软件按 Qwen Image 2.1 的写法生成的（Picture N、灰色 = 要补的空）；别的模型认不认要自己试。
