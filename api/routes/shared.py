"""Shared settings, models, resources and uploads."""

import asyncio
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, Request

from api.schemas.studio import UploadRequest
from pixelle_video.platform.security import SECRET_FIELDS, redact


def mount(api, service):
    upload_validation_lock = asyncio.Semaphore(1)

    @api.get("/settings")
    async def settings():
        from pixelle_video.config import config_manager

        return redact(config_manager.config.to_dict(), mask=True)

    @api.patch("/settings")
    async def update_settings(body: dict, request: Request):
        from pixelle_video.config import config_manager

        allowed = {"project_name", "llm", "api_providers", "tts_models", "tts", "media", "template"}
        if set(body) - allowed:
            raise ValueError("Unknown settings section")

        def clean(value):
            if isinstance(value, dict):
                return {
                    k: clean(v)
                    for k, v in value.items()
                    if not (k in SECRET_FIELDS and v == "••••••••")
                }
            return value

        from urllib.parse import urlparse

        def validate_patch(patch, reference):
            for key, value in patch.items():
                if key not in reference:
                    raise ValueError("未知设置参数: " + key)
                if isinstance(reference[key], dict):
                    if not isinstance(value, dict):
                        raise ValueError("设置格式无效")
                    validate_patch(value, reference[key])
                if key in {"base_url", "local_proxy"} and value:
                    parsed = urlparse(value)
                    if (
                        parsed.scheme not in {"http", "https"}
                        or not parsed.hostname
                        or parsed.username
                        or parsed.password
                    ):
                        raise ValueError("请输入有效的 HTTP/HTTPS 地址")

        validate_patch(body, config_manager.config.to_dict())
        config_manager.update(clean(body))
        config_manager.save()
        return redact(config_manager.config.to_dict(), mask=True)

    @api.get("/models")
    async def models():
        from pixelle_video.config import config_manager
        from pixelle_video.services.api_media import APIProviderMediaService

        cfg = config_manager.config

        def configured(provider):
            provider = {"seedream": "ark", "seedance": "ark"}.get(provider, provider)
            item = getattr(cfg.api_providers, provider, None)
            return bool(
                item
                and (
                    getattr(item, "api_key", "")
                    or (getattr(item, "access_key", "") and getattr(item, "secret_key", ""))
                )
            )

        items = APIProviderMediaService(cfg.to_dict()).list_workflows()
        return {
            "images": [
                m for m in items if m["media_type"] == "image" and configured(m["provider"])
            ],
            "videos": [
                m
                for m in items
                if m["media_type"] == "video"
                and configured(m["provider"])
                and m.get("api_contract_verified")
                and "first_frame_i2v" in m.get("adapter_ability_types", [])
            ],
            "tts_api_configured": bool(cfg.tts_models.providers.minimax.api_key),
            "llm_configured": cfg.is_llm_configured(),
        }

    @api.get("/resources")
    async def resources(request: Request):
        p = service(request)
        items = p.resources.list()
        result = []
        for item in items:
            if item["kind"] in {"template_preview", "preview"}:
                continue
            item = dict(item)
            if item["kind"] == "template":
                item["preview_url"] = item["metadata"].get("preview_url")
            item.pop("object_key", None)
            result.append(item)
        return result

    @api.get("/resources/{asset_id}/url")
    async def resource_url(asset_id: str, request: Request):
        p = service(request)
        asset = p.resources.get(asset_id)
        if asset["kind"] == "template":
            raise HTTPException(403, "模板仅用于服务器渲染")
        return {"url": p.storage.signed_url(asset["object_key"]), "expires_in": 900}

    @api.post("/uploads")
    async def upload(body: UploadRequest, request: Request):
        from pixelle_video.platform.database import now

        if (body.kind == "bgm") != body.content_type.startswith("audio/"):
            raise ValueError("文件类型与资源用途不匹配")
        p = service(request)
        asset_id = uuid4().hex
        ext = {
            "audio/mpeg": ".mp3",
            "audio/wav": ".wav",
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
        }[body.content_type]
        key = f"resources/{body.kind}/{asset_id}{ext}"
        p.database.execute(
            "INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?)",
            (asset_id, key, body.kind, body.name, body.size, body.content_type, "{}", 0, now()),
        )
        return {
            "id": asset_id,
            "url": p.storage.signed_url(key, "PUT"),
            "headers": {"x-cos-acl": "private"},
            "expires_in": 900,
        }

    @api.post("/uploads/{asset_id}/confirm")
    async def confirm_upload(asset_id: str, request: Request):
        from pixelle_video.platform.upload_validation import validate_upload
        from pixelle_video.services.persistence import file_digest

        async with upload_validation_lock:
            p = service(request)
            row = p.database.one("SELECT * FROM assets WHERE id=?", (asset_id,))
            if not row:
                raise HTTPException(404, "上传不存在")
            if row["confirmed"]:
                return {"id": asset_id}
            head = await p.storage.head(row["object_key"])
            if (
                int(head["Content-Length"]) != row["size"]
                or head.get("Content-Type", "").split(";")[0] != row["content_type"]
            ):
                raise ValueError("文件大小或类型与上传申请不一致")
            with tempfile.TemporaryDirectory(prefix="pixelle-upload-") as directory:
                path = Path(directory) / ("media" + Path(row["object_key"]).suffix)
                await p.storage.download(row["object_key"], path, max_bytes=row["size"])
                await asyncio.to_thread(validate_upload, path, row["content_type"], row["size"])
                digest = await asyncio.to_thread(file_digest, path)
                # The client only receives PUT permission for the pending key.
                # Publish the checked bytes at a separate key it cannot overwrite.
                key = f"resources/{row['kind']}/{asset_id}/{digest}{path.suffix}"
                await p.storage.upload(key, path, row["content_type"])
                p.database.execute(
                    "UPDATE assets SET object_key=?, confirmed=1 WHERE id=? AND confirmed=0",
                    (key, asset_id),
                )
        return {"id": asset_id}

    @api.get("/voices")
    async def voices(request: Request, mode: str = "local"):
        if mode == "local":
            from pixelle_video.tts_voices import EDGE_TTS_VOICES

            names = {
                "zh-CN-XiaoxiaoNeural": "晓晓 · 温柔女声",
                "zh-CN-XiaoyiNeural": "晓伊 · 活泼女声",
                "zh-CN-YunjianNeural": "云健 · 沉稳男声",
                "zh-CN-YunxiNeural": "云希 · 清朗男声",
                "zh-CN-YunyangNeural": "云扬 · 知性男声",
            }
            return [
                {"id": v["id"], "name": names.get(v["id"], v["id"]), "locale": v["locale"]}
                for v in EDGE_TTS_VOICES
            ]
        from pixelle_video.services.tts_service import TTSService

        client = TTSService({}).client()
        result = await client.list_voices()
        groups = result.get("voices", result) if isinstance(result, dict) else result
        if isinstance(groups, dict):
            groups = [v for group in groups.values() if isinstance(group, list) for v in group]
        return [
            {
                "id": v.get("voice_id") or v.get("id"),
                "name": v.get("voice_name") or v.get("name") or v.get("voice_id"),
            }
            for v in groups
        ]
