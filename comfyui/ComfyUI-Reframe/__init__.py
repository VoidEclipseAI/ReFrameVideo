"""Reframe nodes (ComfyUI V3 node API): the MiniMax H3 inpaint mask, and input / output marker nodes
that let the Reframe app drive any image-editing or video workflow (see io_nodes.py).

No third-party node dependencies. Nothing here calls ComfyUI's own /prompt endpoint:
the client queues the workflow, these nodes only compute tensors.

Copyright (c) 2026 VoidEclipseAI. Licensed under AGPL-3.0-only (see LICENSE).
"""
from typing_extensions import override

from comfy_api.latest import ComfyExtension, io

from .h3_inpaint_mask import ReframeH3InpaintMask
try:   # the IO marker nodes must never stop the H3 node (video generation needs it) from loading
    from .io_nodes import NODES as IO_NODES
except Exception as e:
    print(f"[ComfyUI-Reframe] IO nodes not loaded: {e!r}")
    IO_NODES = []
try:   # the app's upload clean-up route (/reframe/input/delete); optional, never blocks the nodes
    from . import cleanup_route  # noqa: F401
except Exception as e:
    print(f"[ComfyUI-Reframe] upload clean-up route not loaded: {e!r}")


class ReframeExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [ReframeH3InpaintMask, *IO_NODES]


async def comfy_entrypoint() -> ReframeExtension:
    return ReframeExtension()
