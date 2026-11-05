"""Persistent task queue with registered content handlers and cooperative cancellation."""

import asyncio
import json
import threading
import uuid

from pixelle_video.platform.database import now

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class JobCancelled(RuntimeError):
    pass


class JobManager:
    def __init__(self, database):
        self.database = database
        self.handlers = {}
        self.worker = None
        self.stopping = threading.Event()

    def register(self, kind, handler):
        self.handlers[kind] = handler

    def submit(self, kind, params, work_id=None, batch_id=None, job_id=None):
        if kind not in self.handlers:
            raise ValueError("Unknown job type")
        if work_id and self.database.one(
            "SELECT id FROM jobs WHERE work_id=? AND status IN ('pending','running')", (work_id,)
        ):
            raise ValueError("该作品正在处理，请等待完成后再操作")
        job_id = job_id or uuid.uuid4().hex
        self.database.execute(
            "INSERT INTO jobs(id,kind,work_id,batch_id,status,params,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                job_id,
                kind,
                work_id,
                batch_id,
                "pending",
                json.dumps(params, ensure_ascii=False),
                now(),
                now(),
            ),
        )
        return self.get(job_id)

    def get(self, job_id):
        row = self.database.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not row:
            return None
        for key in ("params", "result"):
            row[key] = json.loads(row[key]) if row[key] else None
        return row

    def list(self, batch_id=None):
        rows = self.database.all(
            "SELECT id FROM jobs"
            + (" WHERE batch_id=?" if batch_id else "")
            + " ORDER BY created_at DESC LIMIT 300",
            (batch_id,) if batch_id else (),
        )
        return [self.get(row["id"]) for row in rows]

    def check_cancel(self, job_id):
        row = self.database.one("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,))
        if self.stopping.is_set() or (row and row["cancel_requested"]):
            raise JobCancelled("任务已中断" if self.stopping.is_set() else "任务已取消")

    def progress(self, job_id, event):
        self.check_cancel(job_id)
        messages = {
            "generating_narrations": "正在创作文案",
            "generating_title": "正在生成标题",
            "generating_image_prompts": "正在构思画面",
            "processing_frame": "正在制作分镜",
            "frame_step": "正在制作分镜",
            "concatenating": "正在合成视频",
            "editing_all_audio": "正在重新生成配音",
        }
        message = messages.get(event.event_type, "正在处理")
        if event.frame_current:
            message += f" {event.frame_current}/{event.frame_total}"
        if event.action:
            message += " · " + {
                "audio": "配音",
                "image": "画面",
                "compose": "排版",
                "video": "片段",
                "skip": "已完成",
            }.get(event.action, event.action)
        self.database.execute(
            "UPDATE jobs SET progress=?,message=?,updated_at=? WHERE id=?",
            (min(0.99, event.progress), message, now(), job_id),
        )

    def cancel(self, job_id):
        row = self.get(job_id)
        if not row or row["status"] in TERMINAL:
            return False
        self.database.execute(
            "UPDATE jobs SET cancel_requested=1,status=CASE WHEN status='pending' THEN 'cancelled' ELSE status END,updated_at=? WHERE id=?",
            (now(), job_id),
        )
        return True

    async def start(self):
        self.database.execute(
            "UPDATE jobs SET status='interrupted',error='服务已重启，可手动恢复',updated_at=? WHERE status='running'",
            (now(),),
        )
        self.stopping.clear()
        self.worker = asyncio.create_task(self._loop())

    async def stop(self):
        self.stopping.set()
        if self.worker:
            await self.worker

    async def _loop(self):
        while not self.stopping.is_set():
            row = self.database.one(
                "SELECT id FROM jobs WHERE status='pending' ORDER BY created_at LIMIT 1"
            )
            if not row:
                await asyncio.sleep(0.3)
                continue
            job = self.get(row["id"])
            self.database.execute(
                "UPDATE jobs SET status='running',message='开始创作',updated_at=? WHERE id=?",
                (now(), job["id"]),
            )
            try:
                self.check_cancel(job["id"])
                result = await asyncio.to_thread(
                    lambda: asyncio.run(self.handlers[job["kind"]](job))
                )
                self.database.execute(
                    "UPDATE jobs SET status='completed',progress=1,message='处理完成',result=?,updated_at=? WHERE id=?",
                    (json.dumps(result, ensure_ascii=False), now(), job["id"]),
                )
            except JobCancelled as error:
                self.database.execute(
                    "UPDATE jobs SET status=?,error=?,updated_at=? WHERE id=?",
                    (
                        "interrupted" if self.stopping.is_set() else "cancelled",
                        str(error),
                        now(),
                        job["id"],
                    ),
                )
            except Exception as error:
                from loguru import logger

                from pixelle_video.platform.security import safe_error

                logger.error("Job failed: {}", safe_error(error))
                self.database.execute(
                    "UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=?",
                    (safe_error(error), now(), job["id"]),
                )
