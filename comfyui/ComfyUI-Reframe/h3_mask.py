"""H3 pixel mask -> latent mask geometry, used by the ReframeH3InpaintMask node. Pure torch, CPU, no ComfyUI imports.
"""
import math

import torch
import torch.nn.functional as F


def frame_groups(frames, ratio, chunk, prepad, drop):
    pattern = [ratio - prepad] + [ratio] * (chunk - 1)
    if min(pattern) < 1 or drop < 0:
        raise ValueError("Invalid H3 temporal geometry")
    cycle = sum(pattern)
    count = max(1, math.ceil(frames / cycle) * chunk - drop)
    starts = [sum(pattern[:i]) for i in range(chunk)]
    groups = []
    for i in range(count):
        start = i // chunk * cycle + starts[i % chunk]
        groups.append((min(start, frames - 1), min(start + pattern[i % chunk], frames)))
    groups[-1] = (groups[-1][0], frames)
    return groups


def map_masks(redraw, body, groups, policy):
    """Binary inputs on the final H3 canvas. One decision per 32px/time cell."""
    if redraw.ndim != 3 or redraw.shape != body.shape:
        raise ValueError("Redraw and body must have identical [frames,height,width] shapes")
    if min(redraw.shape) < 1 or redraw.shape[1] % 32 or redraw.shape[2] % 32:
        raise ValueError("H3 canvas height/width must be multiples of 32; no automatic resize")
    if policy not in ("redraw_wins", "body_wins"):
        raise ValueError("Unknown conflict policy")
    for x in (redraw, body):
        if not torch.isfinite(x).all() or not ((x == 0) | (x == 1)).all():
            raise ValueError("Masks must contain only 0 and 1; threshold explicitly upstream")
    redraw, body = redraw.float().cpu(), body.float().cpu()
    def reduce(x):
        spatial = F.max_pool2d(x[:, None], 32, 32)[:, 0]
        return torch.stack([spatial[s:e].amax(0) for s, e in groups]).bool()
    r, b = reduce(redraw), reduce(body)
    conflicts = r & b
    final = r if policy == "redraw_wins" else r & ~b
    latent = final.float().repeat_interleave(2, 1).repeat_interleave(2, 2)
    def expand(x):
        cells = x.float().repeat_interleave(32, 1).repeat_interleave(32, 2)
        out = torch.zeros_like(redraw)
        for i, (s, e) in enumerate(groups):
            out[s:e] = cells[i]
        return out
    actual, conflict_view = expand(final), expand(conflicts)
    report = {
        "policy": policy, "input_shape": list(redraw.shape),
        "latent_mask_shape": list(latent.shape), "cell_pixels": 32,
        "frame_groups_start_inclusive_end_exclusive": groups,
        "spatial_grow": 0, "temporal_grow": 0,
        "redraw_cells": int(r.sum()), "body_cells": int(b.sum()),
        "conflicting_cells": int(conflicts.sum()), "output_cells": int(final.sum()),
        "requested_redraw_pixels_suppressed": int(((redraw > 0) & (actual == 0)).sum()),
        "body_pixels_in_output_mask": int(((body > 0) & (actual > 0)).sum()),
        "note": "Mask coverage only; not the VAE receptive field or a pixel-preservation guarantee",
    }
    return latent, actual, conflict_view, report
