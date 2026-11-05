"""In-memory object storage for isolated tests only."""

import json
from pathlib import Path


class MemoryStorage:
    def __init__(self):
        self.objects = {}
        self.uploads = []
        self.fail = False

    async def upload(self, key, path, content_type=None):
        if self.fail:
            raise RuntimeError("COS unavailable")
        self.objects[key] = Path(path).read_bytes()
        self.uploads.append(key)
        return key

    async def put_json(self, key, value):
        if self.fail:
            raise RuntimeError("COS unavailable")
        self.objects[key] = json.dumps(value).encode()
        return key

    async def get_json(self, key):
        return json.loads(self.objects[key])

    async def download(self, key, path, max_bytes=None):
        if max_bytes is not None and len(self.objects[key]) > max_bytes:
            raise ValueError("文件大小超过上传申请")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(self.objects[key])
        return str(path)

    async def head(self, key):
        return {"Content-Length": len(self.objects[key]), "Content-Type": "image/png"}

    def signed_url(self, key, method="GET", seconds=900, download_name=None):
        return "https://private-cos.test/" + key + "?signed=1"

    async def delete(self, key):
        self.objects.pop(key, None)

    async def delete_prefix(self, prefix):
        for key in list(self.objects):
            if key.startswith(prefix.rstrip("/") + "/"):
                del self.objects[key]
