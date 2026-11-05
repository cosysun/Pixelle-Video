"""Video script, voice and template previews."""

import asyncio
import tempfile
from pathlib import Path

from fastapi import HTTPException, Request

from api.schemas.studio import CreateVideoRequest


def mount(api, service):
    template_render_lock = asyncio.Semaphore(1)

    @api.post("/preview/script")
    async def script(body: CreateVideoRequest):
        from pixelle_video.config import config_manager
        from pixelle_video.services.llm_service import LLMService
        from pixelle_video.utils.content_generators import (
            generate_script_preview,
            split_narration_script,
        )

        if body.mode == "fixed":
            return {
                "title": body.title or "",
                "narrations": await split_narration_script(body.text, body.split_mode),
            }
        return await generate_script_preview(
            LLMService(config_manager.config.to_dict()),
            body.text,
            body.n_scenes,
            body.min_narration_words,
            body.max_narration_words,
            body.content_style,
            body.title,
        )

    @api.post("/preview/voice")
    async def voice_preview(body: dict, request: Request):
        from pixelle_video.services.tts_service import TTSService

        text = str(body.get("text", "每一个想法，都值得被看见。"))[:200]
        with tempfile.TemporaryDirectory(prefix="pixelle-voice-") as directory:
            path = Path(directory) / "preview.mp3"
            await TTSService({})(
                text=text,
                inference_mode=body.get("mode", "local"),
                voice=body.get("voice"),
                voice_id=body.get("voice_id"),
                model=body.get("model"),
                speed=body.get("speed", 1),
                output_path=str(path),
            )
            asset = await service(request).resources.register_file(path, "preview", name="配音试听")
            return {
                "url": service(request).storage.signed_url(asset["object_key"]),
                "expires_in": 900,
            }

    @api.post("/preview/template")
    async def template_preview(body: dict, request: Request):
        async with template_render_lock:
            if await request.is_disconnected():
                raise HTTPException(499, "预览请求已取消")
            from pixelle_video.platform.template_previews import render_template_preview

            p = service(request)
            asset = await render_template_preview(p, body)
            return {"url": p.storage.signed_url(asset["object_key"]), "expires_in": 900}
