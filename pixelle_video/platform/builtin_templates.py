"""Bundled templates are application assets, independent of COS."""

import hashlib
import json
from pathlib import Path

from pixelle_video.services.frame_html import HTMLFrameGenerator
from pixelle_video.utils.template_util import get_template_type, parse_template_size

COVER_VERSION = "1"


def template_catalog(root):
    root = Path(root)
    manifest_path = root / "frontend/public/template-previews/manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    result = {}
    for path in sorted((root / "templates").glob("*/*.html")):
        if not path.name.startswith(("static_", "image_", "video_")):
            continue
        relative = "/".join(path.parts[-2:])
        asset_id = "tpl_" + hashlib.sha256(relative.encode()).hexdigest()[:24]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        version = hashlib.sha256((COVER_VERSION + digest).encode()).hexdigest()[:16]
        width, height = parse_template_size(str(path))
        metadata = {
            "template_path": relative,
            "template_type": get_template_type(path.name),
            "width": width,
            "height": height,
            "orientation": "portrait" if height > width else "landscape" if width > height else "square",
            "parameters": HTMLFrameGenerator(str(path)).parse_template_parameters(),
            "builtin": True,
        }
        cover = manifest.get(asset_id, {})
        if cover.get("version") == version:
            metadata["preview_url"] = "/template-previews/" + cover["file"]
        result[asset_id] = {
            "id": asset_id,
            "object_key": "builtin://" + relative,
            "kind": "template",
            "name": path.stem,
            "size": path.stat().st_size,
            "content_type": "text/html",
            "metadata": metadata,
            "confirmed": 1,
            "created_at": "",
            "version": version,
            "source": path,
        }
    return result
