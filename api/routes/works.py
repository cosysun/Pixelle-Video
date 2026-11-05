"""Works library and registered video editing."""

from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from api.schemas.studio import EditRequest, ResumeRequest
from pixelle_video.platform.security import redact


def mount(api, service):
    @api.get("/works")
    async def works(
        request: Request,
        page: int = 1,
        page_size: int = 12,
        status: str | None = None,
        search: str = "",
        sort_by: str = "created_at",
        kind: str | None = None,
    ):
        return await service(request).persistence.list_tasks_paginated(
            page=max(1, page),
            page_size=min(60, max(1, page_size)),
            status=status,
            search=search,
            sort_by=sort_by,
            kind=kind,
        )

    @api.get("/works/{work_id}")
    async def work(work_id: str, request: Request):
        p = service(request)
        manifest = await p.persistence._manifest(work_id)
        if not manifest:
            raise HTTPException(404, "作品不存在")
        return redact(manifest)

    @api.get("/works/{work_id}/media")
    async def work_media(
        work_id: str,
        request: Request,
        field: str = "video_path",
        frame: int | None = None,
        download: bool = False,
    ):
        p = service(request)
        manifest = await p.persistence._manifest(work_id)
        if not manifest:
            raise HTTPException(404, "作品不存在")
        if frame is None:
            ref = (manifest["metadata"].get("result") or {}).get(field)
            if field == "cover":
                frames = (manifest.get("storyboard") or {}).get("frames", [])
                ref = next(
                    (
                        f.get("composed_image_path") or f.get("image_path")
                        for f in frames
                        if f.get("composed_image_path") or f.get("image_path")
                    ),
                    None,
                )
        else:
            frames = (manifest.get("storyboard") or {}).get("frames", [])
            if (
                frame < 0
                or frame >= len(frames)
                or field
                not in {
                    "audio_path",
                    "image_path",
                    "video_path",
                    "composed_image_path",
                    "video_segment_path",
                }
            ):
                raise ValueError("无效分镜素材")
            ref = frames[frame].get(field)
        if not ref or not ref.startswith("cos://") or not ref[6:].startswith(f"works/{work_id}/"):
            raise HTTPException(404, "素材尚未生成")
        title = (manifest.get("storyboard") or {}).get("title", "作品")
        url = p.storage.signed_url(ref[6:], download_name=title + ".mp4" if download else None)
        return RedirectResponse(url, status_code=307)

    @api.post("/works/{work_id}/resume")
    async def resume(work_id: str, body: ResumeRequest, request: Request):
        p = service(request)
        manifest = await p.persistence._manifest(work_id)
        if not manifest:
            raise HTTPException(404, "作品不存在")
        if manifest["metadata"].get("status") == "completed":
            raise ValueError("作品已完成，可选择再次创作")
        params = {
            **manifest["metadata"].get("input", {}),
            **body.model_dump(exclude_unset=True),
            "resume_task_id": work_id,
        }
        if body.first_frame_workflow:
            params["api_video_params"] = {
                **(params.get("api_video_params") or {}),
                "first_frame_workflow": body.first_frame_workflow,
            }
        if params.get("media_workflow") and not params["media_workflow"].startswith("api/"):
            raise ValueError("旧工作流已移除，请选择直连 API 模型")
        if params.get("tts_inference_mode") not in {"api", "local"}:
            raise ValueError("旧配音服务已移除，请选择新的配音")
        return p.jobs.submit("video.generate", params, work_id=work_id)

    @api.post("/works/{work_id}/edit")
    async def edit(work_id: str, body: EditRequest, request: Request):
        p = service(request)
        row = p.persistence._row(work_id)
        if not row:
            raise HTTPException(404, "作品不存在")
        if row["kind"] != "video":
            raise ValueError("此接口只支持视频编辑")
        if body.values.get("bgm_id") and p.resources.get(body.values["bgm_id"])["kind"] != "bgm":
            raise ValueError("请选择背景音乐资源")
        if (
            body.action == "replace_template"
            and p.resources.get(body.values["template_id"])["kind"] != "template"
        ):
            raise ValueError("请选择模板资源")
        if (body.values.get("media_overrides") or {}).get("media_workflow"):
            from pixelle_video.services.api_media import APIProviderMediaService

            info = APIProviderMediaService({}).resolve_workflow(
                body.values["media_overrides"]["media_workflow"]
            )
            manifest = await p.persistence._manifest(work_id)
            from pixelle_video.utils.template_util import get_template_type

            kind = get_template_type(Path(manifest["storyboard"]["config"]["frame_template"]).name)
            if info["media_type"] != kind:
                raise ValueError("画面模型与作品模板类型不匹配")
        return p.jobs.submit("video.edit", body.model_dump(), work_id=work_id)

    @api.delete("/works/{work_id}")
    async def delete_work(work_id: str, request: Request):
        p = service(request)
        if not p.persistence._row(work_id):
            raise HTTPException(404, "作品不存在")
        return p.jobs.submit("work.delete", {}, work_id=work_id)
