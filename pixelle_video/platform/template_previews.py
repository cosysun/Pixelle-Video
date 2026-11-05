"""Bundled covers are static web assets; personal previews remain in COS."""

import json
import tempfile
from pathlib import Path

from pixelle_video.services.frame_html import HTMLFrameGenerator
from pixelle_video.utils.os_util import temporary_workspace

SAMPLE_TITLE = "让想法成为作品"
SAMPLE_TEXT = "从一个灵感开始，讲述属于你的故事。"


def sample_image(path):
    from PIL import Image, ImageDraw

    sample = Image.new("RGB", (600, 600), "#eee9df")
    draw = ImageDraw.Draw(sample)
    draw.ellipse((80, 80, 520, 520), fill="#d7decd")
    draw.rounded_rectangle((180, 210, 440, 410), radius=20, fill="#b9a89c")
    draw.line((230, 265, 390, 265), fill="#f5f0e8", width=8)
    draw.line((230, 300, 365, 300), fill="#f5f0e8", width=8)
    sample.save(path)


async def render_template_preview(platform, body):
    with (
        tempfile.TemporaryDirectory(prefix="pixelle-preview-") as directory,
        temporary_workspace(directory),
    ):
        params = await platform.resources.prepare_video_params(body)
        template = platform.resources.get(params["template_id"])
        generator = HTMLFrameGenerator(params["frame_template"])
        path = Path(directory) / "preview.png"
        sample_path = Path(directory) / "sample.png"
        sample_image(sample_path)
        try:
            await generator.generate_frame(
                title=str(body.get("title", SAMPLE_TITLE))[:200],
                text=str(body.get("text", SAMPLE_TEXT))[:500],
                image=str(sample_path),
                ext=body.get("template_params") or {},
                output_path=str(path),
            )
            return await platform.resources.register_file(
                path,
                "preview",
                name=template["name"] + " · 风格效果图",
                metadata={"template_id": template["id"]},
            )
        finally:
            await HTMLFrameGenerator.close_browser()


async def prepare_template_covers(root, force=False):
    from pixelle_video.platform.builtin_templates import template_catalog

    root = Path(root)
    output = root / "frontend/public/template-previews"
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    report = {"generated": 0, "skipped": 0, "failed": []}
    for template in template_catalog(root).values():
        asset_id = template["id"]
        version = template["version"]
        filename = asset_id + "-" + version + ".webp"
        if not force and manifest.get(asset_id, {}).get("version") == version and (output / filename).is_file():
            report["skipped"] += 1
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="pixelle-cover-") as directory:
                sample = Path(directory) / "sample.png"
                sample_image(sample)
                png = Path(directory) / "preview.png"
                generator = HTMLFrameGenerator(str(template["source"]))
                try:
                    await generator.generate_frame(
                        SAMPLE_TITLE, SAMPLE_TEXT, str(sample), output_path=str(png)
                    )
                    from PIL import Image

                    with Image.open(png) as image:
                        image.thumbnail((720, 1000))
                        image.convert("RGB").save(output / filename, "WEBP", quality=85)
                finally:
                    await HTMLFrameGenerator.close_browser()
            old = manifest.get(asset_id, {}).get("file")
            manifest[asset_id] = {"version": version, "file": filename}
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            if old and old != filename:
                (output / Path(old).name).unlink(missing_ok=True)
            report["generated"] += 1
        except Exception as error:
            report["failed"].append({"template": template["name"], "error": type(error).__name__})
    return report
