"""Reframe input / output marker nodes.

They let the Reframe app drive ANY image-editing or video workflow. Each node just passes its input through, so the
workflow runs normally inside ComfyUI with whatever you connected (a Load Image, a typed prompt, a seed).
When the app runs the workflow (exported with "Export (API)"), it finds these nodes by type and replaces
what feeds them: its own images, mask, prompt, seed and size; and it reads the result from Reframe Output.
"""
from comfy_api.latest import io

CATEGORY = "Reframe/IO"
VALUES = ("seed", "width", "height", "frames")
TEXTS = ("prompt", "user")
VIDEOS = ("rough_cut", "guide")


class ReframeImageInput(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="ReframeImageInput",
            display_name="Reframe Image Input",
            category=CATEGORY,
            description=(
                "Marks where the Reframe app puts an image. index 1 = the image to edit (gray 128 = empty areas to fill); "
                "2, 3, ... = reference images in the order the app lists them (Picture 2, Picture 3, ...). "
                "Video workflows: 1, 2, ... = the reference images the user ticked (first frame, last frame, background, "
                "character sheets, ...), in the order the prompt calls Picture 1, Picture 2, ... "
                "Inside ComfyUI it passes the connected image through, so connect a Load Image for testing."
            ),
            inputs=[
                io.Image.Input("image", tooltip="Any image for testing. The app replaces it."),
                io.Int.Input("index", default=1, min=1, max=16, tooltip="1 = image to edit; 2+ = reference images, in order."),
            ],
            outputs=[io.Image.Output("out", display_name="image")],
        )

    @classmethod
    def execute(cls, image, index) -> io.NodeOutput:
        return io.NodeOutput(image)


class ReframeMaskInput(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="ReframeMaskInput",
            display_name="Reframe Mask Input",
            category=CATEGORY,
            description=(
                "Marks where the Reframe app puts the repaint mask (white = repaint, same size as image 1). "
                "When the app repaints the whole image it sends an all-white mask. Optional: leave this node out "
                "if the workflow has no inpainting; the app then pastes the result back inside its own mask. "
                "Video workflows: one mask per frame of the rough cut (white = empty areas and blend band to repaint)."
            ),
            inputs=[io.Mask.Input("mask", tooltip="Any mask for testing. The app replaces it.")],
            outputs=[io.Mask.Output("out", display_name="mask")],
        )

    @classmethod
    def execute(cls, mask) -> io.NodeOutput:
        return io.NodeOutput(mask)


class ReframeTextInput(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="ReframeTextInput",
            display_name="Reframe Text Input (prompt)",
            category=CATEGORY,
            description=(
                "Marks where the Reframe app puts the prompt. Connect the output to your text encoder's text input. "
                "Video workflows: name = prompt gets the app's full prompt (written for MiniMax H3, with the empty-area "
                "analysis); name = user gets only what the user typed. Image workflows ignore name."
            ),
            inputs=[
                io.String.Input("text", multiline=True, default="", tooltip="A prompt for testing. The app replaces it."),
                io.Combo.Input("name", options=list(TEXTS), default="prompt", optional=True,
                               tooltip="Video workflows: prompt = the app's full prompt; user = only the user's own text."),
            ],
            outputs=[io.String.Output("out", display_name="text")],
        )

    @classmethod
    def execute(cls, text, name="prompt") -> io.NodeOutput:
        return io.NodeOutput(text)


class ReframeIntInput(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="ReframeIntInput",
            display_name="Reframe Number Input (seed / size)",
            category=CATEGORY,
            description=(
                "Marks where the Reframe app puts a number: seed, or the width / height of image 1. "
                "Video workflows: width / height = the size of the rough cut the app sends (from the output-resolution "
                "setting), frames = its frame count (the original clip's; pad it yourself if the model needs 4k+1 etc.). "
                "Connect the output to the sampler's seed, or to a width / height / length input."
            ),
            inputs=[
                io.Combo.Input("name", options=list(VALUES), default="seed", tooltip="Which number the app puts here."),
                io.Int.Input("value", default=0, min=0, max=2**53 - 1, tooltip="A value for testing. The app replaces it."),
            ],
            outputs=[io.Int.Output("out", display_name="value")],
        )

    @classmethod
    def execute(cls, name, value) -> io.NodeOutput:
        return io.NodeOutput(value)


class ReframeOutput(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="ReframeOutput",
            display_name="Reframe Output",
            category=CATEGORY,
            description=(
                "Marks the result the Reframe app takes back (exactly one of these per workflow): one image, or for video "
                "workflows the decoded frames (same frame rate as the input; extra frames at the end are cut off). "
                "It passes the images through: connect a Save Image / Preview / Create Video after it to see the result in ComfyUI."
            ),
            inputs=[io.Image.Input("images", tooltip="The finished image, or the frames for video workflows.")],
            outputs=[io.Image.Output("out", display_name="images")],
        )

    @classmethod
    def execute(cls, images) -> io.NodeOutput:
        return io.NodeOutput(images)


class ReframeVideoInput(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="ReframeVideoInput",
            display_name="Reframe Video Input",
            category=CATEGORY,
            description=(
                "Video workflows: marks where the Reframe app puts a video, as frames. name = rough_cut: the edited clip "
                "(empty areas are flat gray 128), at the width / height and frame count the app sends; name = guide: the "
                "same rough cut scaled to 768 wide (H3 uses it as <Video 1>). The repaint mask goes into Reframe Mask Input "
                "(one mask per frame, white = repaint). Inside ComfyUI it passes the connected frames through."
            ),
            inputs=[
                io.Image.Input("images", tooltip="Any frames for testing (e.g. Load Video -> Get Video Components). The app replaces them."),
                io.Combo.Input("name", options=list(VIDEOS), default="rough_cut", tooltip="Which video the app puts here."),
            ],
            outputs=[io.Image.Output("out", display_name="images")],
        )

    @classmethod
    def execute(cls, images, name) -> io.NodeOutput:
        return io.NodeOutput(images)


NODES = [ReframeImageInput, ReframeVideoInput, ReframeMaskInput, ReframeTextInput, ReframeIntInput, ReframeOutput]
