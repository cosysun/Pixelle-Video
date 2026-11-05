"""Registered COS resources and temporary processing downloads."""

import json
import mimetypes
import uuid
from pathlib import Path

from pixelle_video.platform.database import now
from pixelle_video.utils.os_util import get_temp_path


class ResourceService:
    def __init__(self, database, storage):
        self.database = database
        self.storage = storage
        from pixelle_video.platform.builtin_templates import template_catalog
        from pixelle_video.utils.os_util import get_pixelle_video_root_path

        self.builtin_templates = template_catalog(get_pixelle_video_root_path())

    def list(self, kind=None):
        rows = self.database.all(
            "SELECT * FROM assets WHERE confirmed=1" + (" AND kind=?" if kind else ""),
            (kind,) if kind else (),
        )
        items = {row["id"]: {**row, "metadata": json.loads(row["metadata"])} for row in rows}
        if kind in {None, "template"}:
            items.update({key: self._public_builtin(value) for key, value in self.builtin_templates.items()})
        return list(items.values())

    @staticmethod
    def _public_builtin(template):
        return {k: v for k, v in template.items() if k not in {"source", "version"}}

    def get(self, asset_id):
        if asset_id in self.builtin_templates:
            return self._public_builtin(self.builtin_templates[asset_id])
        row = self.database.one("SELECT * FROM assets WHERE id=? AND confirmed=1", (asset_id,))
        if not row:
            raise ValueError("资源不存在或尚未上传完成")
        return {**row, "metadata": json.loads(row["metadata"])}

    async def register_file(self, path, kind, name=None, metadata=None, asset_id=None):
        path = Path(path)
        from pixelle_video.services.persistence import file_digest

        digest = file_digest(path)
        asset_id = asset_id or uuid.uuid4().hex
        key = f"resources/{kind}/{asset_id}/{digest}{path.suffix}"
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        await self.storage.upload(key, path, mime)
        self.database.execute(
            "INSERT OR REPLACE INTO assets VALUES(?,?,?,?,?,?,?,?,?)",
            (
                asset_id,
                key,
                kind,
                name or path.stem,
                path.stat().st_size,
                mime,
                json.dumps(metadata or {}, ensure_ascii=False),
                1,
                now(),
            ),
        )
        return self.get(asset_id)

    async def materialize(self, asset_id, destination=None):
        asset = self.get(asset_id)
        dest = Path(
            destination
            or get_temp_path("resources", asset["id"] + Path(asset["object_key"]).suffix)
        )
        if asset_id in self.builtin_templates:
            import shutil

            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.builtin_templates[asset_id]["source"], dest)
        else:
            await self.storage.download(asset["object_key"], dest)
        return str(dest)

    async def prepare_video_params(self, params):
        params = dict(params)
        template_id = params.get("template_id") or params.get("frame_template")
        candidates = self.list("template")
        template = next(
            (
                a
                for a in candidates
                if a["id"] == template_id or a["metadata"].get("template_path") == template_id
            ),
            None,
        )
        if not template:
            raise ValueError("请选择可用的视频模板")
        parameters = template["metadata"].get("parameters", {})
        if set(params.get("template_params") or {}) - set(parameters):
            raise ValueError("模板包含不支持的自定义选项")
        relative = template["metadata"]["template_path"]
        if (
            Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or Path(relative).suffix != ".html"
        ):
            raise ValueError("模板资源路径无效")
        template_path = Path(get_temp_path("resources", "templates", relative))
        await self.materialize(template["id"], template_path)
        params["frame_template"] = str(template_path)
        params["template_id"] = template["id"]
        from pixelle_video.services.frame_html import HTMLFrameGenerator

        params["media_width"], params["media_height"] = HTMLFrameGenerator(
            str(template_path)
        ).get_media_size()
        bgm_id = params.get("bgm_id") or params.get("bgm_path")
        if bgm_id:
            if isinstance(bgm_id, str) and bgm_id.startswith("asset://"):
                bgm_id = bgm_id[8:]
            bgm = next(
                (
                    a
                    for a in self.list("bgm")
                    if a["id"] == bgm_id or a["name"] == Path(str(bgm_id)).stem
                ),
                None,
            )
            if not bgm:
                raise ValueError("背景音乐不存在，请重新选择")
            params["bgm_path"] = await self.materialize(bgm["id"])
            params["bgm_id"] = bgm["id"]
        else:
            params["bgm_path"] = None
        return params

    async def prepare_storyboard(self, storyboard):
        params = await self.prepare_video_params(
            {"frame_template": storyboard.config.frame_template}
        )
        storyboard.config.frame_template = params["frame_template"]
        return storyboard
