# Edit in ReFrameVideo, run in your own ComfyUI

English · [中文](tutorial-comfyui-zh.md) · [Tutorial](tutorial-en.md)

ReFrameVideo does the editing: detect subjects, move and scale them, change the aspect ratio, set the redraw area and pick a first frame. ComfyUI runs the models.
**Export** packs everything the model will receive into a zip file: the prefilled rough cut, the mask, reference images, the prompt and the workflow. You can run it as is in your own ComfyUI, or change the workflow yourself.

Use this when you want to:
- Run on another computer or a cloud ComfyUI.
- Change steps, seed or LoRAs yourself, or plug the inputs into your own workflow.
- See exactly what the app sends to the model.

## 1. Prepare ComfyUI

You need the same setup as for generating from the app. See the [installation guide](../../docs/INSTALL.md):
- A ComfyUI version with MiniMax H3 support (tested on 0.45.5).
- The `ComfyUI-Reframe` node pack. Copy the whole `comfyui/ComfyUI-Reframe/` folder from this repository to `ComfyUI/custom_nodes/`, then restart ComfyUI. The `ReframeH3InpaintMask` node in the workflow comes from it.
- The H3 video model, text encoder, both VAEs and the LoRAs (the ReFrameVideo LoRA; the Pika speed LoRA is recommended for 6 steps).

## 2. Edit in the app

Follow section 3 of the [tutorial](tutorial-en.md) up to "Prepare the first-frame reference": detect, move, change the frame, press M to check the redraw area, and use a first frame in Image results.

Then, in the Video generation panel, tick the reference images to send, leave the prompt empty or write your own, and set the resolution and seed. Export uses the settings in this panel.

## 3. Export the video generation inputs

1. Stay on the Video editing page.
2. Click Export in the right icon bar, then Export video generation inputs.
3. This step does not run H3. It builds the rough cut, and when Empty-area analysis is on, a vision model describes each empty area (about 1 minute; it waits in the queue). The zip file downloads when it is ready.
4. The same zip file is also saved in the `导出export` folder inside the project folder. Use Show in folder to find it.

The zip file contains:

| File | What it is |
|---|---|
| `rough_cut.mp4` | The rough cut: your edit, with empty areas filled with gray 128. Already scaled to the H3 canvas and padded to 17k+5 frames |
| `mask.mov` | The redraw mask (white = redraw). PNG-encoded and lossless. **Do not convert it to MP4**: H.264 turns 255 into 254 |
| `video1_guide.mp4` | The `<Video 1>` reference video (the rough cut at 768 px wide) |
| `picture_N_*.png` | Reference images. N matches `<Picture N>` in the prompt (for example `picture_1_first_frame.png`) |
| `partial_mask.mov` | Only present when Subject redraw strength is above 0 |
| `prompt.txt` | The prompt (Ref2VA six-section format) |
| `workflow_api.json` | The ComfyUI workflow (API format), with file names already set to the names in the zip file |
| `说明.txt` | A short description of these files (in Chinese) |

## 4. Run it in ComfyUI

1. Unzip the file. Copy the videos and images (`.mp4`, `.mov`, `.png`) directly into `ComfyUI/input/`, not into a subfolder.
2. Drag `workflow_api.json` into the ComfyUI page (or open it from the menu).
3. Check the model loader nodes. The model and LoRA file names come from the app's settings at export time. If your files have other names, select yours in those nodes.
4. Run it. The output video is in `ComfyUI/output/videops/`.

Nodes you may want to change:

| To change | Node |
|---|---|
| Seed | `RandomNoise` |
| Steps | `BasicScheduler` (6 steps with the Pika speed LoRA) |
| LoRAs and strength | `LoraLoaderModelOnly` |
| Prompt | `prompt` in `MiniMaxH3ReferenceToVideo` |
| Reference images | `LoadImage` (the file names must match) |

## 5. Differences from generating in the app

After H3 finishes, the app does a few more things that a plain ComfyUI run does not:
- **Frame count**: H3 needs 17k+5 frames, so the rough cut is padded at the end. The app keeps only as many frames as the source video. Trim the end of your output to the source frame count.
- **Audio**: the app puts the source video's audio back on the output.
- **Color correction**: the app matches the overall color to the source video using the areas that are not redrawn.
- **Second pass**: when Second pass (HD) is on, the app runs a second, higher-resolution pass. The exported workflow has only the first pass.

## 6. Export image-edit inputs only (first or last frame)

To make the first frame with your own image model:
1. In Image editing, open the First frame or Last frame tab and write the prompt.
2. Click Export, then Export image-edit inputs.
3. The zip file contains `input.png` (empty areas in gray 128), `mask.png` (white = empty), the reference images, `prompt.txt`, and a `workflow_api.json` for Qwen Image 2.1 or MiniMax H3 Image (the model selected in the app). Use it the same way: copy the images to `ComfyUI/input/`, open the workflow and run it.
4. You can bring the image back: in the reference list of the Video generation panel, click Change on the First frame row, then Upload an image. The uploaded image becomes the selected first frame.

## 7. Export a result you already generated

On the Video results page, click Export, then Zip this result and its inputs. Besides the output and the source video, the zip file has every input sent to H3 and the workflow (the same files as in section 3). Use it to reproduce the result or to rerun it with another seed.

The repository also includes a ready example: [workflows/ReFrameVideo_H3_example_workflow_api.json](../../workflows/ReFrameVideo_H3_example_workflow_api.json), the workflow behind the tutorial example's final video.
