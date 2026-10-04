"""Console-local templates and response helpers."""
import json as _json
import os
import time

from fastapi.templating import Jinja2Templates

from api import __version__, runtime_paths

WEBUI_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = str(runtime_paths.temp_dir())

templates = Jinja2Templates(directory=os.path.join(WEBUI_DIR, "templates"))
templates.env.globals["asset_v"] = str(int(time.time()))
templates.env.globals["app_version"] = __version__


def _sse(lines):
    """Encode an iterable of strings as Server-Sent Events."""
    for line in lines:
        yield f"data: {_json.dumps(line)}\n\n"
