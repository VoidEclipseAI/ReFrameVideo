"""HTTP route the Reframe app calls after a workflow finishes: delete the files it uploaded.

POST /reframe/input/delete  {"names": ["reframe/videops_xxxxxxxx_name.png", ...]}

Only files directly inside <input>/reframe/ whose name starts with "videops_" can be deleted
(that is what the app uploads); anything else is refused. Missing files are skipped.
"""
import os

import folder_paths
from aiohttp import web
from server import PromptServer

SUB = "reframe"
PREFIX = "videops_"


def _target(name):
    if not isinstance(name, str):
        return None
    sub, sep, base = name.replace("\\", "/").partition("/")
    if sep != "/" or sub != SUB or not base.startswith(PREFIX) or "/" in base or base in (".", ".."):
        return None
    root = os.path.realpath(os.path.join(folder_paths.get_input_directory(), SUB))
    path = os.path.realpath(os.path.join(root, base))
    return path if os.path.dirname(path) == root else None


@PromptServer.instance.routes.post("/reframe/input/delete")
async def delete_inputs(request):
    try:
        names = (await request.json()).get("names") or []
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)
    if not isinstance(names, list):
        return web.json_response({"error": "names must be a list"}, status=400)
    deleted, refused = 0, []
    for n in names:
        path = _target(n)
        if path is None:
            refused.append(n)
            continue
        try:
            os.remove(path)
            deleted += 1
        except FileNotFoundError:
            pass
        except OSError as e:
            refused.append(f"{n}: {e}")
    return web.json_response({"deleted": deleted, "refused": refused})
