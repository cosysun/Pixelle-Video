"""Operator tools: COS resources and repeatable history import."""

import argparse
import asyncio
import json
import re
import tempfile
from pathlib import Path

from dotenv import load_dotenv


async def import_resources(platform, root):
    from pixelle_video.platform.builtin_templates import template_catalog
    from pixelle_video.services.frame_html import HTMLFrameGenerator
    from pixelle_video.utils.template_util import get_template_type, parse_template_size

    count = 0
    bundled = template_catalog(root)
    platform.resources.builtin_templates.update(bundled)
    count += len(bundled)
    for folder in (root / "data/templates",):
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*/*.html")):
            if not path.name.startswith(("static_", "image_", "video_")):
                continue
            width, height = parse_template_size(str(path))
            generator = HTMLFrameGenerator(str(path))
            params = generator.parse_template_parameters()
            # A deterministic ID makes repeated imports and custom overrides safe.
            relative = "/".join(path.parts[-2:])
            import hashlib

            asset_id = "tpl_" + hashlib.sha256(relative.encode()).hexdigest()[:24]
            metadata = {
                "template_path": relative,
                "template_type": get_template_type(path.name),
                "width": width,
                "height": height,
                "orientation": "portrait"
                if height > width
                else "landscape"
                if width > height
                else "square",
                "parameters": params,
            }
            await platform.resources.register_file(
                path, "template", metadata=metadata, asset_id=asset_id
            )
            count += 1
    for folder in (root / "bgm", root / "data/bgm"):
        if folder.exists():
            for path in sorted(folder.iterdir()):
                if path.suffix.lower() in {".mp3", ".wav"}:
                    import hashlib

                    asset_id = "bgm_" + hashlib.sha256(path.name.encode()).hexdigest()[:24]
                    await platform.resources.register_file(path, "bgm", asset_id=asset_id)
                    count += 1
    return count


async def import_history(platform, root):
    """Do not touch source files. Commit only after every referenced file uploads."""
    from pixelle_video.services.persistence import FILE_FIELDS
    from pixelle_video.utils.os_util import temporary_workspace

    report = {"imported": [], "skipped": [], "failed": []}
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or not (directory / "metadata.json").is_file():
            continue
        task_id = directory.name
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", task_id):
            report["failed"].append({"id": task_id, "error": "Invalid history ID"})
            continue
        if platform.persistence._row(task_id):
            report["skipped"].append(task_id)
            continue
        try:
            metadata = json.loads((directory / "metadata.json").read_text())
            storyboard_path = directory / "storyboard.json"
            storyboard = (
                json.loads(storyboard_path.read_text()) if storyboard_path.is_file() else None
            )

            def remap(value, key=""):
                if isinstance(value, dict):
                    return {k: remap(v, k) for k, v in value.items()}
                if isinstance(value, list):
                    return [remap(v) for v in value]
                if key in FILE_FIELDS and value and not str(value).startswith("cos://"):
                    source = Path(value)
                    candidates = [
                        directory / "frames" / source.name,
                        directory / source.name,
                        source,
                    ]
                    found = next(
                        (
                            p
                            for p in candidates
                            if p.is_file() and directory.resolve() in p.resolve().parents
                        ),
                        None,
                    )
                    if not found:
                        raise ValueError("Missing artifact: " + source.name)
                    return str(found.resolve())
                if key == "frame_template" and value:
                    parts = Path(value).parts
                    return "/".join(parts[-2:]).replace("/default.html", "/image_default.html")
                if key == "ref_audio":
                    return None
                return value

            metadata = remap(metadata)
            if metadata.get("status") == "running":
                metadata["status"] = "interrupted"
                metadata["error"] = "导入的任务尚未完成，可选择直连模型继续创作"
            metadata["task_id"] = task_id
            if storyboard:
                storyboard = remap(storyboard)
                storyboard["config"]["task_id"] = task_id
            # Keep local legacy paths out of COS metadata; resources have their own registry.
            inp = metadata.setdefault("input", {})
            music = inp.get("bgm_path")
            if music:
                asset = next(
                    (a for a in platform.resources.list("bgm") if a["name"] == Path(music).stem),
                    None,
                )
                inp["bgm_id"] = asset["id"] if asset else None
                inp["bgm_path"] = "asset://" + asset["id"] if asset else None
            with (
                tempfile.TemporaryDirectory(prefix="pixelle-import-") as tmp,
                temporary_workspace(tmp),
                platform.persistence.operation(task_id),
            ):
                # Upload raw manifest fields to preserve compatibility without dropping old data.
                state = __import__(
                    "pixelle_video.services.persistence", fromlist=["_operation"]
                )._operation.get()
                if storyboard:
                    state["storyboard"] = await platform.persistence._remote_files(
                        task_id, storyboard
                    )
                await platform.persistence.save_task_metadata(task_id, metadata)
                manifest = await platform.persistence._manifest(task_id)
                if manifest["metadata"]["task_id"] != task_id:
                    raise RuntimeError("Manifest verification failed")
            report["imported"].append(task_id)
        except Exception as error:
            report["failed"].append({"id": task_id, "error": str(error)[:300]})
    return report


async def run(args):
    if args.command == "investment-migrate":
        import os

        from pixelle_video.investment.migration import migrate_legacy
        from pixelle_video.investment.service import InvestmentService
        from pixelle_video.platform.database import Database

        database = Database(os.getenv("PIXELLE_DATABASE", "data/platform.sqlite3"))
        try:
            report = migrate_legacy(database, Path(args.root).resolve())
            engine = InvestmentService(database)
            engine.rebuild_history()
            print(json.dumps(report, ensure_ascii=False, indent=2))
        finally:
            database.close()
        return
    if args.command == "template-covers":
        from pixelle_video.platform.template_previews import prepare_template_covers

        report = await prepare_template_covers(Path(args.root).resolve(), force=args.force)
        print(json.dumps(report, ensure_ascii=False))
        if report["failed"]:
            raise SystemExit(1)
        return
    from pixelle_video.platform.runtime import Platform

    platform = Platform()
    try:
        if args.command == "resources":
            print(
                json.dumps(
                    {"resources": await import_resources(platform, Path(args.root).resolve())},
                    ensure_ascii=False,
                )
            )
        elif args.command == "history":
            report = await import_history(platform, Path(args.root).resolve())
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if report["failed"]:
                raise SystemExit(1)
        elif args.command == "cleanup":
            await platform.cleanup_expired_assets()
            print("Expired temporary COS assets cleaned")
    finally:
        platform.database.close()


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    investment = commands.add_parser("investment-migrate", help="迁移旧投资账本，保留源文件")
    investment.add_argument("--root", required=True, help="旧 portfolio-index 项目路径")
    resources = commands.add_parser("resources", help="Import bundled and custom resources to COS")
    resources.add_argument("--root", default=".")
    history = commands.add_parser(
        "history", help="Import old output histories without deleting originals"
    )
    history.add_argument("--root", default="output")
    commands.add_parser("cleanup", help="Clean expired previews and abandoned uploads in COS")
    covers = commands.add_parser("template-covers", help="Build bundled template covers as web assets")
    covers.add_argument("--force", action="store_true", help="Regenerate existing covers")
    covers.add_argument("--root", default=".")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
