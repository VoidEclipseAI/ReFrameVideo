# ReFrameVideo Beginner Guide

This guide assumes the application, required models, and services are already configured. It is not an installation guide.

[中文版](tutorial-zh.md)

## The basic workflow

ReFrameVideo edits one video at a time. Detect objects as layers, move or hide them, use reference images to tell the AI what the picture should look like, then generate a new video.

**Open video → Detect objects → Edit the layout → Generate and select reference images → Generate video → Review.**

A checkerboard in the editor means empty space. It is not the finished result. Generating the video fills those areas.

## 1. Start the application

1. In the folder where the app is installed, double-click `start.bat`, or run `python start.py` from that folder.
2. Wait for the browser to open. The default frontend address is `http://127.0.0.1:8765/`.
3. Keep the launch window open while working. Closing it stops the services started by that launcher.
4. Open Settings with the gear icon at the top right. Choose the interface language and appearance.
5. Go to Settings → Services and click Check service status. The dot next to ComfyUI engine in the top bar is green when the connection works and red when it does not. If you see the mock engine, you can test the interface but cannot create real generations.

For a hosted deployment, use the address provided by its administrator. The ComfyUI address is set by the deployer. A cloud API provider is something you configure yourself under Settings → Cloud API (in one of three request formats); it is only used when the Image generation or Video generation panel is switched to API, and it does not configure the H3 video service on the GPU computer.

## 2. Find your way around

| Area | Purpose |
|---|---|
| Project tabs at the top | Open and switch projects; the All results button on the right shows every generated result |
| Video editing | Detect objects and edit video layers |
| Image editing | Prepare first-frame, last-frame, background, and character references |
| Image results | Review candidates and select one to use |
| Video results | Review generated videos |
| Left toolbar | Tools (Select / move, Hand, Click detect, Draw mask, Frame touch-up), view options, flips, and reset |
| Right panel rail | Layers, Generate, Mask, Transform, Frame, Version, Export, and History |
| Timeline | Playback, frame stepping, and time-range selection |
| Task monitor | Running status, queued tasks, and the task log |

Hover over a button or name to see its description. When unsure, keep the defaults and change one setting at a time.

You can switch right-panel groups at any time. Temporary operations are handled automatically: dragging the frame ends; click detection clears its points; if you have drawn a mask, a dialog asks what to do: Create it, then switch, Discard, or Stay here; frame touch-up saves automatically when you leave it.

## 3. First exercise: move a person

### Open a video

1. Click + at the top or press `Ctrl+O` to open the "Open video or project" dialog.
2. Enter a video file path and open it, or choose one of the Existing projects.
3. For a video longer than 10 seconds, the app asks whether to Edit in segments or Edit as one. For your first exercise, choose Edit as one. Long videos are covered in section 6.
4. Wait for preview preparation, then play the video to check it.

The path must be accessible to the computer running the backend. A path on your own computer will not work if the backend runs on another machine that cannot access that file.

### Detect the person

1. Open the Layers panel on the right and find Subject matte detection.
2. Choose a detection method:
   - Text prompt: type an English name such as `person`. SAM 3.1 finds all subjects of that type.
   - Click prompt: click the subject on the canvas (left-click to include, right-click to exclude unwanted parts).
3. Click Detect and wait for the task to finish.
4. If several people are found, click one in the layer list or on the canvas to select the person you want to edit.
5. Step through the beginning, middle, and end of the clip. Check that the correct person is selected and the mask covers the body.

For your first exercise, use a short clip with a clearly visible, slowly moving subject. Frame range limits only the detection; it does not trim later operations.

### Move the person

1. Select the person and drag them to the target position.
2. For precise changes, use X, Y, width, height, and rotation in Transform.
3. Checkerboard space left at the original position is expected. Generation fills that background.
4. Step through the clip to check that the moved subject stays inside the frame.
5. Press `Ctrl+Z` to undo if needed.

Start with a simple move. Avoid combining movement, rotation, scaling, and frame extension in your first attempt.

### Prepare the first-frame reference

1. Click + in the page bar at the top and choose First frame to create a new image tab, or open the existing one.
2. In Image editing, check that the image matches the current person position. If it is outdated, recapture the image from the video.
3. Describe the result you want in the Description box in Generate. English usually works best. For example:

   > A person stands on the left side of the scene. Restore the background naturally in the empty area on the right.

   This is an example for moving someone left. Adapt it to your actual positions, subject, and scene.
4. Click Generate candidates. Start with a few (for example 2–4). Check whether the empty area is filled naturally, whether the person is undistorted, and whether an extra person appears.
5. In Image results, click the best candidate and confirm it shows "✓ Selected". **Generating candidates does not select one.**
6. If the final frame changes substantially, prepare and select a Last frame in the same way.

If you do not yet have a background image, the app warns you when you create a First or Last frame. Make one first (see section 6); the frames will match the scene better.

References should agree with the current edit. If you later move the person, change the frame, or change masks, candidates become Outdated. Check them and regenerate as needed.

### Generate the video

1. Return to Video editing and open the Generate panel on the right.
2. At the top of Video generation, choose ComfyUI or API. ComfyUI (MiniMax H3 + LoRA on the GPU computer) is the default.
3. Check the Reference list. Each item is numbered Picture N, matching the prompt. Confirm that the intended first frame, last frame, and background are included.
4. Keep the H3 model and LoRA provided by your deployment unless you understand their requirements. Sampling steps default to 20; with the Pika H3 Flex speed-up LoRA, use 6 steps.
5. Choose the Output resolution:
   - Preset: pick a level.
   - Short edge: choose the short side yourself.
   - Free: enter a maximum width and height.
   The result is exactly the size H3 generates. The aspect ratio always follows the frame.
6. We recommend leaving Your prompt empty and keeping System prompt on automatic: the software writes the whole prompt for you. Empty-area analysis uses a vision model by default: it compares the rough cut and the first frame and writes what each empty area should contain. Choose Off to use a generic description instead (faster). Only fill in Your prompt (English works best) if the automatic result is wrong and you need to describe what happens in the video.
7. Click Generate video and follow progress in the task monitor. It usually takes several to more than ten minutes, depending on clip length, models, and hardware.
8. When complete, open Video results. The same inputs with the same Seed give nearly the same result.

**Cloud API:** Click API at the top of Video generation to call the video model of the provider you selected in Settings. The app sends only the rough cut (empty areas in gray), the checked references, and your prompt, with no extra wording. Switching shows a warning: in the author's tests, Seedance could not complete video fill, so use it with care. Choose Use the API anyway to continue. API mode does not run the local H3 and LoRA.

### Review the result

Check the beginning, middle, and end, and any frames where the person is occluded or touches the frame edge:

- Is the person in the intended position, with continuous motion?
- Is the original position filled naturally, without an extra person?
- Are there flickering edges, white outlines, or incomplete limbs?
- Do extended areas contain gray patches, black bars, or abrupt changes?

If the result is not right, first decide whether the problem comes from detection, editing, or the reference image, then change only that step. Do not increase every mask setting at once.

## 4. Two more exercises

### Remove an object

1. Detect and select the object.
2. Use the eye button on its layer row, or press `H`, to **hide the layer**. Its original location becomes an empty area.
3. Check the empty area, then prepare a first-frame reference without that object.
4. Select a candidate and generate the video.

Do not delete the layer as a substitute for hiding it. Deleting removes that layer's edits, and the original object may reappear. The default Vacated shape is Rect ×3, which splits the hole into rectangles instead of the person's outline. If the model still paints a person into the hole, check that Vacated shape is Rect ×3 for that layer in the Mask panel, then check your reference image again.

### Extend the frame

1. In the Frame panel on the right, choose an aspect ratio (such as 16:9 or 2.39:1), or enter how many pixels to extend each side in Extend each side by (negative numbers crop). You can also press `F` and drag the frame's edges and corners on the canvas.
2. Check the hatched area in the frame: the source video does not contain it, so it will be generated.
3. Prepare a new first-frame reference, selecting a candidate where the extended area is fully filled.
4. Prepare a last frame if needed, then generate.

If the extended first frame still has gray patches, do not expect video generation to fix them automatically. Improve the reference image first.

## 5. When to adjust masks and matte settings

Most of these controls are in the Layers and Mask panels on the right.

| Control | Use it when… |
|---|---|
| Matte view (`A`) | You want to inspect the selected subject's matte as white on black |
| Subject matte refine: Shrink / grow | You need to adjust the outline; positive grows, negative shrinks |
| Fill holes | The matte contains small gaps |
| Remove specks | Small stray pieces appear around the subject |
| Temporal smoothing | The matte flickers between frames; large values can lag fast motion |
| Subject redraw area: Outer ring redraw | You want a ring of extra redraw outside the subject (negative values stop edge pixels from being pasted back) |
| Subject extension | You want the model to paint beyond one side of the subject; click + to add one, enter pixels, or drag the orange arrows on the canvas |
| Complete cut-off part | A subject clipped by the source frame edge has been moved into view |
| Vacated shape | You need to change the hole left by a moved or hidden subject |
| Global blend (all layers): Blend band width | Makes the newly generated area blend more naturally with the kept picture (0 = none) |

Frame touch-up (in the left toolbar) edits the matte on one frame with a brush. Check neighboring frames afterward. Keep part of subject (in Mask) paints a region on the subject that follows it.

The "This image" settings in Image editing apply only to that reference image. They do not change the video layers; layer positions still follow the video.

## 6. Backgrounds, long videos, versions, export, and history

**Backgrounds:** In Image editing, create a Background image tab. Use Extract background first (it pulls a background without the subject from the video), then Generate background, and select and use a candidate. A background image can be used for First and Last frames, or with Background prefill in Mask, so generation fills empty areas with it instead of gray (those areas are still redrawn). After changing the frame, extract the background again so it covers the new area.

**Long videos (Edit in segments):** For a video longer than 10 seconds, choose Edit in segments. The app cuts by shot automatically and divides any overly long part evenly. Use the segment numbers on the left to jump to a segment. Each segment is its own project, edited and generated as usual. On the segment page:
- Drag the vertical cut lines, or double-click the thumbnail strip, or press `S`, to add a cut (+ Add cut (S)).
- Edit this segment creates the project for that segment. If you change cuts on a segment that is already being edited, its project is first moved to the recycle bin, then cut again for the new range.
- Merge with next removes the cut between two segments.
- Export full video joins the segments in order, using the result you chose for each segment; segments without a result use the source video.

**Versions:** In the Version panel on the right, the + button creates a new version (Duplicate the current version, or start from the source video). Duplicate the current version before trying another approach, so you keep both sets of layer edits. The ⋯ menu on each version row lets you rename, Duplicate version, Show in folder, or Delete this version. Deleting a version removes its video and the files it uses, and this cannot be undone. The source video cannot be deleted. Check which version is selected before you delete it.

**Export:** The Export panel on the right packs the inputs for a step into a zip file for download. It includes the image or video the model receives, the prompt, and a ComfyUI workflow, so you can rerun it on your own ComfyUI:
- Export video generation inputs: rough cut, masks, Picture N references, prompt, and workflow.
- Export image-edit inputs: input, mask, references, and prompt for the current image.
- In Image results: Download the selected one, Zip the chosen one, or Zip all candidates.
- In Video results: Download this video, or Zip this result and its inputs.

Exported zip files are also saved in the `导出export` folder inside the project folder. Use Show in folder to find the files.

**History:** The History panel lists each step from the project's action log. Layer and frame edits can be restored with Restore to before. Restoring is also a step, so you can undo it. Merges, deletes, detection, and generation are listed but cannot be restored. You can turn off action logging in Settings → Performance.

**All results:** The All results button at the top lists every video and image generated across all projects, newest first. You can filter by type and project. Click an item to open its project and jump to its result.

## 7. Troubleshooting

| Symptom | Check first |
|---|---|
| No real generated video | Whether the mock engine is active (the top-bar dot should be green); the task monitor log and the model service |
| Checkerboard remains | You may still be viewing the edit preview; empty areas are filled during generation |
| Output follows an old layout | Whether candidates are Outdated; whether one is selected (✓ Selected); whether the run includes the correct references |
| A removed person reappears | Whether the selected first frame actually removes them; make sure Vacated shape is Rect ×3 and check the background reference |
| Preview is slow | In Settings → Performance, lower Preview quality, or use Cache clip on the timeline; recache after changing layers |
| A mask prompt appears when switching panels | The mask is not finished: choose Create it, then switch, Discard, or Stay here |
| Panels are misplaced | Settings → Interface and help → Reset layout |
| Chinese remains in English mode | Custom names, user input, and some raw backend logs keep their original text |

## 8. Essential shortcuts

| Key | Action |
|---|---|
| `Ctrl+O` | Open video or project |
| Space | Play / pause; hold Space and drag to pan the canvas |
| `,` / `.` | Previous / next frame |
| `I` / `O` | Set the start / end of the time range |
| `Ctrl+Z` / `Ctrl+Shift+Z` | Undo / redo |
| `H` | Show / hide the selected ordinary layer; enable / disable fixed masks |
| `Delete` or `Backspace` | Delete the selected layer (asks first) |
| `M` / `A` | Show redraw regions / matte view |
| `F` | Edit the frame |
| `L` / `P` | Open the Layers / Mask panel |
| `Shift+0` / `Shift+1` / `Shift+2` | Fit frame / show all / fit selection |
| `Ctrl+=` / `Ctrl+-` | Zoom in / out |
| Hold `C` | Temporarily view the source video |
| `Esc` | Deselect or close the current overlay; follow mode-specific instructions |

In frame touch-up: `B` brush, `E` eraser (hold Alt to switch), `W` magic wand, `V` move, `[` `]` brush size, `,` `.` change frame, `Enter` save, `Esc` cancel.

Your first milestone is completing Open → Detect → Move → Select first frame → Generate → Review without assistance. Afterward, explore backgrounds, segment editing, complex masks, and multiple versions.
