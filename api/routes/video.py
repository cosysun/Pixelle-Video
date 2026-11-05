"""Video generation, batch and task operations."""

from uuid import uuid4

from fastapi import HTTPException, Request

from api.schemas.studio import BatchRequest, CreateVideoRequest
from pixelle_video.platform.security import redact


def mount(api, service):
    def validate_generation(body, p):
        asset = p.resources.get(body.template_id)
        kind = asset["metadata"].get("template_type", "image")
        from pixelle_video.services.api_media import APIProviderMediaService

        if asset["kind"] != "template":
            raise ValueError("请选择视频模板")
        if body.bgm_id and p.resources.get(body.bgm_id)["kind"] != "bgm":
            raise ValueError("请选择背景音乐资源")
        if kind in {"image", "video"}:
            info = APIProviderMediaService({}).resolve_workflow(body.media_workflow or "")
            if info["media_type"] != kind:
                raise ValueError("画面模型与模板类型不匹配")
        if kind == "video":
            if not body.first_frame_workflow:
                raise ValueError("动态视频需要先选择首帧图片模型")
            first = APIProviderMediaService({}).resolve_workflow(body.first_frame_workflow)
            if first["media_type"] != "image":
                raise ValueError("首帧需要使用图片模型")
            if not info.get("api_contract_verified") or "first_frame_i2v" not in info.get(
                "adapter_ability_types", []
            ):
                raise ValueError("此视频模型尚未验证首帧生成能力")

    @api.post("/video/generate")
    async def generate(body: CreateVideoRequest, request: Request):
        from pixelle_video.utils.os_util import create_task_id

        p = service(request)
        validate_generation(body, p)
        work_id = create_task_id()
        return p.jobs.submit(
            "video.generate", body.generation_params(), work_id=work_id, job_id=work_id
        )

    @api.post("/video/batch")
    async def batch(body: BatchRequest, request: Request):
        from pixelle_video.utils.os_util import create_task_id

        p = service(request)
        validate_generation(body.settings, p)
        topics = [t.strip() for t in body.topics if t.strip()]
        if not topics:
            raise ValueError("请输入至少一个主题")
        batch_id = uuid4().hex
        jobs = []
        for topic in topics:
            work_id = create_task_id()
            params = body.settings.generation_params()
            params.update(text=topic, mode="generate")
            jobs.append(
                p.jobs.submit(
                    "video.generate", params, work_id=work_id, job_id=work_id, batch_id=batch_id
                )
            )
        return {"batch_id": batch_id, "jobs": jobs}

    @api.get("/jobs")
    async def jobs(request: Request, batch_id: str | None = None):
        return redact(service(request).jobs.list(batch_id))

    @api.get("/jobs/{job_id}")
    async def job(job_id: str, request: Request):
        row = service(request).jobs.get(job_id)
        if not row:
            raise HTTPException(404, "任务不存在")
        return redact(row)

    @api.post("/jobs/{job_id}/cancel")
    async def cancel(job_id: str, request: Request):
        return {"success": service(request).jobs.cancel(job_id)}

    @api.post("/jobs/{job_id}/retry")
    async def retry_job(job_id: str, request: Request):
        p = service(request)
        job = p.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        if job["status"] not in {"failed", "cancelled", "interrupted"}:
            raise ValueError("该任务无需重试")
        params = dict(job["params"])
        if job["kind"] == "video.generate" and p.persistence._row(job["work_id"]):
            params["resume_task_id"] = job["work_id"]
        return p.jobs.submit(job["kind"], params, work_id=job["work_id"], batch_id=job["batch_id"])
