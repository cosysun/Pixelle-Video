"""COS manifests and immutable artifacts; SQLite provides the searchable directory."""

import asyncio
import copy
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

from pixelle_video.platform.database import now
from pixelle_video.platform.security import redact, safe_error
from pixelle_video.services.serialization import StoryboardSerializer
from pixelle_video.utils.os_util import get_output_path

_operation = ContextVar("pixelle_persistence_operation", default=None)


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


FILE_FIELDS = {
    "audio_path",
    "image_path",
    "video_path",
    "composed_image_path",
    "video_segment_path",
    "final_video_path",
}


class PersistenceService(StoryboardSerializer):
    def __init__(self, storage=None, database=None, **kwargs):
        self.storage = storage
        self.database = database

    @contextmanager
    def operation(self, task_id, editing=False):
        token = _operation.set(
            {
                "task_id": task_id,
                "editing": editing,
                "storyboard": None,
                "metadata": None,
                "downloads": {},
                "uploaded": set(),
                "local_storyboard": None,
            }
        )
        try:
            yield
        finally:
            _operation.reset(token)

    def get_task_dir(self, task_id):
        return Path(get_output_path(task_id))

    def _row(self, task_id):
        return self.database.one("SELECT * FROM works WHERE id=?", (task_id,))

    async def _manifest(self, task_id, checkpoint=True):
        row = self._row(task_id)
        key = (
            (
                row.get("manifest")
                if row.get("status") == "completed"
                else row.get("checkpoint") or row.get("manifest")
            )
            if row and checkpoint
            else (row.get("manifest") if row else None)
        )
        return await self.storage.get_json(key) if key else None

    async def _remote_files(self, task_id, data):
        async def convert(value, key=""):
            if isinstance(value, dict):
                return {
                    k: await convert(v, k)
                    for k, v in value.items()
                    if not callable(v) and k not in {"progress_callback", "output_path"}
                }
            if isinstance(value, list):
                return [await convert(v) for v in value]
            if key == "frame_template" and value and hasattr(self, "resources"):
                for asset in self.resources.list("template"):
                    relative = asset["metadata"].get("template_path", "")
                    if relative and str(value).endswith(relative):
                        return relative
            if (
                key == "bgm_path"
                and value
                and hasattr(self, "resources")
                and not str(value).startswith("asset://")
            ):
                for asset in self.resources.list("bgm"):
                    if Path(str(value)).name == asset["id"] + Path(asset["object_key"]).suffix:
                        return "asset://" + asset["id"]
                return None
            if key in FILE_FIELDS and value and not str(value).startswith("cos://"):
                path = Path(value)
                if not path.is_file():
                    raise FileNotFoundError(f"素材不存在: {path.name}")
                digest = await asyncio.to_thread(file_digest, path)
                object_key = f"works/{task_id}/artifacts/{digest}{path.suffix}"
                state = _operation.get()
                if not state or object_key not in state["uploaded"]:
                    await self.storage.upload(object_key, path)
                    if state:
                        state["uploaded"].add(object_key)
                return "cos://" + object_key
            return value

        return await convert(data)

    async def _local_files(self, task_id, data):
        state = _operation.get()
        if state is None:
            return data

        async def convert(value, key=""):
            if isinstance(value, dict):
                return {
                    k: await convert(v, k)
                    for k, v in value.items()
                    if not callable(v) and k not in {"progress_callback", "output_path"}
                }
            if isinstance(value, list):
                return [await convert(v) for v in value]
            if key in FILE_FIELDS and isinstance(value, str) and value.startswith("cos://"):
                object_key = value[6:]
                if object_key not in state["downloads"]:
                    dest = self.get_task_dir(task_id) / "restored" / Path(object_key).name
                    await self.storage.download(object_key, dest)
                    state["downloads"][object_key] = str(dest)
                return state["downloads"][object_key]
            return value

        return await convert(data)

    def set_local_storyboard(self, storyboard):
        _operation.get()["local_storyboard"] = self._storyboard_to_dict(storyboard)

    def set_local_metadata(self, metadata):
        _operation.get()["metadata"] = copy.deepcopy(metadata)

    async def load_task_metadata(self, task_id):
        state = _operation.get()
        if state and state.get("metadata") is not None:
            return copy.deepcopy(state["metadata"])
        manifest = await self._manifest(task_id)
        if not manifest:
            return None
        result = await self._local_files(task_id, manifest["metadata"])
        if state:
            state["metadata"] = copy.deepcopy(result)
        return result

    async def load_storyboard(self, task_id):
        state = _operation.get()
        if state and state.get("local_storyboard") is not None:
            return self._dict_to_storyboard(copy.deepcopy(state["local_storyboard"]))
        data = state.get("storyboard") if state else None
        if data is None:
            manifest = await self._manifest(task_id)
            data = manifest.get("storyboard") if manifest else None
        if not data:
            return None
        data = await self._local_files(task_id, copy.deepcopy(data))
        return self._dict_to_storyboard(data)

    async def save_storyboard(self, task_id, storyboard):
        state = _operation.get()
        if state is None:
            raise RuntimeError("Storyboard writes require a task operation")
        state["local_storyboard"] = self._storyboard_to_dict(storyboard)
        state["storyboard"] = await self._remote_files(
            task_id, self._storyboard_to_dict(storyboard)
        )
        if not state["editing"]:
            metadata = state.get("metadata") or await self.load_task_metadata(task_id)
            if metadata:
                await self._commit(task_id, metadata, final=False)

    async def save_task_metadata(self, task_id, metadata):
        state = _operation.get()
        if state is None:
            raise RuntimeError("Metadata writes require a task operation")
        state["metadata"] = copy.deepcopy(metadata)
        if (
            state["editing"]
            and metadata.get("status") != "completed"
            and (self._row(task_id) or {}).get("manifest")
        ):
            return
        await self._commit(task_id, metadata, final=metadata.get("status") == "completed")

    async def _commit(self, task_id, metadata, final=False):
        state = _operation.get()
        safe = await self._remote_files(task_id, redact(metadata))
        safe["task_id"] = task_id
        safe["config"] = {k: v for k, v in safe.get("config", {}).items() if k in {"llm_model"}}
        safe["input"] = {
            k: v
            for k, v in safe.get("input", {}).items()
            if not callable(v) and k not in {"progress_callback", "task_id", "output_path"}
        }
        sb = state.get("storyboard")
        if sb is None:
            previous = await self._manifest(task_id)
            sb = previous.get("storyboard") if previous else None
        key = f"works/{task_id}/revisions/{uuid4().hex}/manifest.json"
        await self.storage.put_json(key, {"version": 1, "metadata": safe, "storyboard": sb})
        result = safe.get("result") or {}
        inp = safe.get("input") or {}
        existing = self._row(task_id)
        summary = {
            "task_id": task_id,
            "id": task_id,
            "kind": "video",
            "title": (sb or {}).get("title")
            or inp.get("title")
            or inp.get("text", "")[:32]
            or "未命名作品",
            "status": safe.get("status", "running"),
            "created_at": (existing or {}).get("created_at") or safe.get("created_at") or now(),
            "completed_at": safe.get("completed_at"),
            "duration": result.get("duration", 0),
            "file_size": result.get("file_size", 0),
            "n_frames": len((sb or {}).get("frames", [])),
            "video_path": result.get("video_path"),
            "error": safe.get("error"),
            "cover": next(
                (
                    f.get("composed_image_path") or f.get("image_path")
                    for f in (sb or {}).get("frames", [])
                    if f.get("composed_image_path") or f.get("image_path")
                ),
                None,
            ),
        }
        current_manifest = key if final else (existing or {}).get("manifest")
        self.database.execute(
            """INSERT INTO works(id,kind,title,status,created_at,updated_at,manifest,checkpoint,summary,search_text)
            VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,status=excluded.status,
            updated_at=excluded.updated_at,manifest=excluded.manifest,checkpoint=excluded.checkpoint,
            summary=excluded.summary,search_text=excluded.search_text""",
            (
                task_id,
                "video",
                summary["title"],
                summary["status"],
                summary["created_at"],
                now(),
                current_manifest,
                key,
                json.dumps(summary, ensure_ascii=False),
                summary["title"] + " " + inp.get("text", ""),
            ),
        )

    async def update_task_status(self, task_id, status, error=None):
        metadata = await self.load_task_metadata(task_id)
        if not metadata:
            return
        metadata["status"] = status
        if error:
            metadata["error"] = safe_error(error)
        metadata["completed_at"] = now()
        await self.save_task_metadata(task_id, metadata)

    async def task_exists(self, task_id):
        return self._row(task_id) is not None

    async def list_tasks_paginated(
        self,
        page=1,
        page_size=12,
        status=None,
        sort_by="created_at",
        sort_order="desc",
        search="",
        kind=None,
    ):
        where = []
        params = []
        for key, value in [("status", status), ("kind", kind)]:
            if value:
                where.append(key + "=?")
                params.append(value)
        if search:
            where.append("search_text LIKE ?")
            params.append("%" + search + "%")
        clause = " WHERE " + " AND ".join(where) if where else ""
        order = {"created_at": "created_at", "title": "title", "updated_at": "updated_at"}.get(
            sort_by, "created_at"
        )
        direction = "ASC" if sort_order == "asc" else "DESC"
        total = self.database.one("SELECT COUNT(*) n FROM works" + clause, params)["n"]
        rows = self.database.all(
            "SELECT summary FROM works"
            + clause
            + f" ORDER BY {order} {direction} LIMIT ? OFFSET ?",
            (*params, page_size, (page - 1) * page_size),
        )
        return {
            "tasks": [json.loads(row["summary"]) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
        }

    async def list_tasks(self, **kwargs):
        return (await self.list_tasks_paginated(page_size=1000, **kwargs))["tasks"]

    async def get_statistics(self):
        rows = self.database.all("SELECT status, COUNT(*) n FROM works GROUP BY status")
        counts = {row["status"]: row["n"] for row in rows}
        return {"total_tasks": sum(counts.values()), **counts}

    async def delete_task(self, task_id):
        if not self._row(task_id):
            return False
        await self.storage.delete_prefix(f"works/{task_id}")
        self.database.execute("DELETE FROM works WHERE id=?", (task_id,))
        return True

    async def rebuild_index(self):
        # SQLite is authoritative for the directory; manifests retain full content.
        return await self.get_statistics()
