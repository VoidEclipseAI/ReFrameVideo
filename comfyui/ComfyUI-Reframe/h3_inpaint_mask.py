"""Reframe H3 Inpaint Mask: pixel masks -> H3 latent noise mask, one explicit decision per cell.

H3 compresses 16x16 pixels into one latent and patchifies 2x2 latents into one token, so the
smallest unit it can repaint is a 32x32 pixel cell spanning one temporal group (1 or 4 frames).
This node makes that rounding explicit instead of hiding it inside a generic resize:
a cell is repainted if any pixel of it, in any frame of its group, is in redraw_mask.

No growing, feathering, cropping or resizing happens here; geometry is read from the VAE and
checked against the encoded latent.
"""
import json

import torch
import torch.nn.functional as F
from comfy_api.latest import io

from .h3_mask import frame_groups, map_masks

CELL = 32           # pixels per H3 token side (16x VAE downscale * 2x2 patch)
PREVIEW_SCALE = 4   # preview is canvas / 4, so one cell = 8x8 preview pixels
CONFLICT = {"redraw": "redraw_wins", "protect": "body_wins"}


def h3_groups(vae, frames):
    """Temporal groups of the H3 VAE, read from the model, never assumed."""
    inner = vae.first_stage_model
    keys = ("vae_ratio_t", "tokens_chunk_size", "frame_pre_padding", "token_drop")
    geometry = {k: getattr(inner, k, None) for k in keys}
    if vae.spacial_compression_encode() != 16 or any(v is None for v in geometry.values()):
        raise ValueError("vae is not the MiniMax H3 video VAE (no H3 temporal geometry found). "
                         "Connect the same VAE that encoded `samples`.")
    ratio, chunk, prepad, drop = [int(v) for v in geometry.values()]
    if (ratio, chunk, prepad) != (4, 5, 3):
        raise ValueError(f"Unsupported H3 VAE geometry {geometry}; this node only knows ratio 4 / chunk 5 / pre-padding 3.")
    if frames < 5 or (frames - 5) % 17:
        raise ValueError(f"Mask has {frames} frames; H3 needs 17k+5 (22, 39, 56, ...). "
                         "Use the same frame count that went into VAE Encode.")
    return geometry, frame_groups(frames, ratio, chunk, prepad, drop)


def binarize(mask, name):
    if mask.ndim == 2:
        mask = mask[None]
    if mask.ndim != 3:
        raise ValueError(f"{name} must be a MASK batch [frames, height, width]; got shape {tuple(mask.shape)}")
    return (mask.float().cpu() > 0.5).float()


def preview(redraw_cells, protect, conflicts):
    """[frames, H/4, W/4, 3]: red = repainted cell, yellow = cell with both, green = protected pixels kept."""
    shrink = lambda x: F.max_pool2d(x[:, None], PREVIEW_SCALE, PREVIEW_SCALE)[:, 0] > 0
    red, both, green = shrink(redraw_cells), shrink(conflicts), shrink(protect)
    img = torch.zeros(*red.shape, 3)
    img[green & ~red] = torch.tensor([0.15, 0.75, 0.25])
    img[red] = torch.tensor([0.85, 0.15, 0.15])
    img[both] = torch.tensor([0.95, 0.80, 0.10])
    return img


class ReframeH3InpaintMask(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="ReframeH3InpaintMask",
            display_name="Reframe H3 Inpaint Mask",
            category="Reframe/H3",
            description=(
                "Turns pixel masks into the noise mask MiniMax H3 actually uses, and attaches it to the latent "
                "(like Set Latent Noise Mask). H3 can only repaint whole 32x32-pixel cells over a temporal group "
                "(1 or 4 frames): a cell is repainted if any of its pixels, in any frame of the group, is in "
                "redraw_mask. Nothing is grown, feathered, cropped or resized."
            ),
            inputs=[
                io.Latent.Input("samples", tooltip="H3 video latent from VAE Encode (the frames to repaint)."),
                io.Vae.Input("vae", tooltip="The H3 VAE that produced `samples`. Used only to read the cell size and frame grouping."),
                io.Mask.Input(
                    "redraw_mask",
                    tooltip="White = repaint. Same frames and size as the video encoded into `samples` "
                            "(17k+5 frames, width and height multiples of 32). Values above 0.5 count as white.",
                ),
                io.Mask.Input(
                    "protect_mask", optional=True,
                    tooltip="Optional. White = keep (e.g. people that must not change). Only matters for cells that "
                            "also contain redraw pixels; see `conflict`. Same frames and size as redraw_mask.",
                ),
                io.Combo.Input(
                    "conflict", options=list(CONFLICT), default="redraw",
                    tooltip="A cell containing both redraw and protect pixels: "
                            "redraw = repaint it (protected edges next to the hole get repainted too); "
                            "protect = keep it (the hole next to protected pixels may stay unfilled).",
                ),
            ],
            outputs=[
                io.Latent.Output("latent", display_name="latent", tooltip="`samples` with the noise mask attached. Connect to the sampler."),
                io.Image.Output(
                    "preview", display_name="preview",
                    tooltip="Per-frame cell map at 1/4 size: red = repainted, yellow = repainted cell that also had "
                            "protect pixels (kept if conflict = protect), green = protected and kept.",
                ),
                io.String.Output("report", display_name="report", tooltip="JSON summary: cell counts, conflicts, frame groups."),
            ],
        )

    @classmethod
    def execute(cls, samples, vae, redraw_mask, conflict, protect_mask=None) -> io.NodeOutput:
        redraw = binarize(redraw_mask, "redraw_mask")
        protect = torch.zeros_like(redraw) if protect_mask is None else binarize(protect_mask, "protect_mask")
        if protect.shape != redraw.shape:
            raise ValueError(f"protect_mask {tuple(protect.shape)} and redraw_mask {tuple(redraw.shape)} "
                             "must have the same frames, height and width.")
        if conflict not in CONFLICT:
            raise ValueError(f"conflict must be one of {list(CONFLICT)}; got {conflict!r}")
        frames, height, width = redraw.shape
        if height % CELL or width % CELL:
            raise ValueError(f"Mask is {width}x{height}; H3 needs width and height that are multiples of {CELL}. "
                             "Resize the video and masks to the same H3 canvas before encoding.")
        geometry, groups = h3_groups(vae, frames)
        latent_in = samples["samples"]
        expected = (len(groups), height // 16, width // 16)
        if latent_in.ndim != 5 or latent_in.shape[0] != 1 or tuple(latent_in.shape[2:]) != expected:
            raise ValueError(f"samples has shape {tuple(latent_in.shape)} but a {frames}-frame {width}x{height} mask "
                             f"needs [1, C, {expected[0]}, {expected[1]}, {expected[2]}]. "
                             "Encode exactly the video these masks were made for.")

        latent_mask, actual, conflicts, stats = map_masks(redraw, protect, groups, CONFLICT[conflict])
        out = samples.copy()   # same as Set Latent Noise Mask
        out["noise_mask"] = latent_mask.reshape((-1, 1, latent_mask.shape[-2], latent_mask.shape[-1]))

        report = {
            "conflict": conflict,
            "canvas": [width, height], "frames": frames,
            "cells_repainted": stats["output_cells"],
            "cells_with_redraw": stats["redraw_cells"],
            "cells_with_protect": stats["body_cells"],
            "cells_with_both": stats["conflicting_cells"],
            "redraw_pixels_left_unpainted": stats["requested_redraw_pixels_suppressed"],
            "protect_pixels_repainted": stats["body_pixels_in_output_mask"],
            "frame_groups": stats["frame_groups_start_inclusive_end_exclusive"],
            "vae_geometry": geometry,
        }
        return io.NodeOutput(out, preview(actual, protect, conflicts), json.dumps(report, indent=2))
