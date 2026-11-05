"""Application composition; content modules register their own handlers."""

import json
import os
import shutil
import tempfile
from pathlib import Path

from pixelle_video.config import config_manager
from pixelle_video.platform.database import Database
from pixelle_video.platform.jobs import JobManager
from pixelle_video.platform.resources import ResourceService
from pixelle_video.platform.storage import COSStorage
from pixelle_video.services.persistence import PersistenceService
from pixelle_video.utils.os_util import temporary_workspace


class Platform:
    def __init__(self, database=None, storage=None):
        temp_dir = Path(os.getenv("PIXELLE_TEMP_DIR", "/tmp/pixelle"))
        temp_dir.mkdir(parents=True, exist_ok=True)
        self.database = database or Database(os.getenv("PIXELLE_DATABASE", "data/platform.sqlite3"))
        config_manager.bind_database(self.database)
        self.storage = storage or COSStorage()
        self.resources = ResourceService(self.database, self.storage)
        self.persistence = PersistenceService(self.storage, self.database)
        self.persistence.resources = self.resources
        self.jobs = JobManager(self.database)
        self.jobs.register("video.generate", self.generate_video)
        self.jobs.register("video.edit", self.edit_video)
        self.jobs.register("work.delete", self.delete_work)

    async def start(self):
        # Reconcile directory status after a crash without repeating provider calls.
        for row in self.database.all("SELECT id,summary FROM works WHERE status='running'"):
            summary = json.loads(row["summary"])
            summary["status"] = "interrupted"
            summary["error"] = "服务已重启，可手动恢复"
            self.database.execute(
                "UPDATE works SET status='interrupted',summary=? WHERE id=?",
                (json.dumps(summary, ensure_ascii=False), row["id"]),
            )
            with self.persistence.operation(row["id"]):
                try:
                    await self.persistence.update_task_status(
                        row["id"], "interrupted", summary["error"]
                    )
                except Exception:
                    from loguru import logger

                    logger.warning("COS 暂不可用，已在数据库标记中断")
        await self.jobs.start()
        self.maintenance = __import__("asyncio").create_task(self._maintenance())

    async def stop(self):
        self.maintenance.cancel()
        try:
            await self.maintenance
        except __import__("asyncio").CancelledError:
            pass
        await self.jobs.stop()

    async def cleanup_expired_assets(self):
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        rows = self.database.all(
            "SELECT id,object_key FROM assets WHERE (kind='preview' OR confirmed=0) AND created_at<?",
            (cutoff,),
        )
        for asset in rows:
            await self.storage.delete(asset["object_key"])
            self.database.execute("DELETE FROM assets WHERE id=?", (asset["id"],))

    async def _maintenance(self):
        import asyncio

        while True:
            try:
                await self.cleanup_expired_assets()
            except Exception:
                from loguru import logger

                logger.warning("临时 COS 资源清理失败，将自动重试")
            await asyncio.sleep(3600)

    async def video_core(self):
        from pixelle_video.service import PixelleVideoCore

        core = PixelleVideoCore(persistence=self.persistence)
        await core.initialize()
        return core

    async def generate_video(self, job):
        work_id = job["work_id"] or job["id"]
        path = tempfile.mkdtemp(prefix="pixelle-job-", dir=os.getenv("PIXELLE_TEMP_DIR"))
        core = None
        try:
            with temporary_workspace(path), self.persistence.operation(work_id):
                core = await self.video_core()
                params = dict(job["params"])
                if params.get("resume_task_id"):
                    stored = await self.persistence.load_task_metadata(work_id)
                    params = {**(stored or {}).get("input", {}), **params}
                params = await self.resources.prepare_video_params(params)
                params["task_id"] = work_id
                params["source_pipeline"] = "quick_create"
                core.cancel_check = lambda: self.jobs.check_cancel(job["id"])
                params["progress_callback"] = lambda event: self.jobs.progress(job["id"], event)
                if params.get("resume_task_id"):
                    sb = await self.persistence.load_storyboard(work_id)
                    if sb:
                        sb.config.frame_template = params["frame_template"]
                        for k in (
                            "media_workflow",
                            "tts_inference_mode",
                            "tts_voice_id",
                            "tts_model",
                        ):
                            if params.get(k) is not None:
                                setattr(sb.config, k, params[k])
                        if params.get("tts_voice"):
                            sb.config.voice_id = params["tts_voice"]
                        for k in ("tts_speed", "tts_volume", "api_video_params"):
                            if params.get(k) is not None:
                                setattr(sb.config, k, params[k])
                        self.persistence.set_local_storyboard(sb)
                await core.generate_video(**params)
                row = self.persistence._row(work_id)
                if not row or row["status"] != "completed":
                    raise RuntimeError("作品上传未完成")
                return {"work_id": work_id}
        finally:
            try:
                if core:
                    await core.cleanup()
            finally:
                shutil.rmtree(path, ignore_errors=True)

    async def edit_video(self, job):
        path = tempfile.mkdtemp(prefix="pixelle-edit-", dir=os.getenv("PIXELLE_TEMP_DIR"))
        core = None
        try:
            with (
                temporary_workspace(path),
                self.persistence.operation(job["work_id"], editing=True),
            ):
                core = await self.video_core()
                core.cancel_check = lambda: self.jobs.check_cancel(job["id"])
                args = job["params"]
                action = args["action"]
                values = dict(args.get("values", {}))
                storyboard = await self.persistence.load_storyboard(job["work_id"])
                if storyboard is None:
                    raise ValueError("作品缺少分镜")
                local_params = await self.resources.prepare_video_params(
                    {"frame_template": storyboard.config.frame_template}
                )
                storyboard.config.frame_template = local_params["frame_template"]
                self.persistence.set_local_storyboard(storyboard)
                metadata = await self.persistence.load_task_metadata(job["work_id"])
                inp = metadata.get("input", {})
                if inp.get("bgm_id") or inp.get("bgm_path"):
                    prepared = await self.resources.prepare_video_params(
                        {
                            "frame_template": local_params["frame_template"],
                            "template_id": local_params["template_id"],
                            "bgm_id": inp.get("bgm_id"),
                            "bgm_path": inp.get("bgm_path"),
                        }
                    )
                    metadata["input"]["bgm_path"] = prepared["bgm_path"]
                    self.persistence.set_local_metadata(metadata)
                if action == "remove_bgm":
                    metadata["input"]["bgm_id"] = None
                    self.persistence.set_local_metadata(metadata)
                if action == "replace_template":
                    prepared = await self.resources.prepare_video_params(
                        {"template_id": values.pop("template_id")}
                    )
                    values["frame_template"] = prepared["frame_template"]
                    metadata["input"]["template_id"] = prepared["template_id"]
                    self.persistence.set_local_metadata(metadata)
                if action == "update_bgm":
                    asset_id = values.pop("bgm_id", None)
                    values["bgm_path"] = (
                        await self.resources.materialize(asset_id) if asset_id else None
                    )
                    metadata["input"]["bgm_id"] = asset_id
                    self.persistence.set_local_metadata(metadata)
                allowed = {
                    "update_title",
                    "update_bgm",
                    "remove_bgm",
                    "regenerate_all_audio",
                    "regenerate_frame_audio",
                    "regenerate_frame_media",
                    "insert_frame",
                    "delete_frame",
                    "replace_template",
                    "recompose",
                }
                if action not in allowed:
                    raise ValueError("Unsupported editing action")
                if action == "recompose":
                    await core.task_editor._rebuild_final_video(
                        job["work_id"], storyboard, metadata
                    )
                else:
                    if action == "regenerate_all_audio":
                        values["progress_callback"] = lambda event: self.jobs.progress(
                            job["id"], event
                        )
                    await getattr(core.history, action)(job["work_id"], **values)
                return {"work_id": job["work_id"]}
        finally:
            try:
                if core:
                    await core.cleanup()
            finally:
                shutil.rmtree(path, ignore_errors=True)

    async def delete_work(self, job):
        await self.persistence.delete_task(job["work_id"])
        return {"deleted": True, "work_id": job["work_id"]}
