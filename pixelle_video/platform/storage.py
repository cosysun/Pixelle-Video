"""Private COS object storage. Local files are temporary processing artifacts."""

import asyncio
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath


class COSStorage:
    def __init__(self):
        from qcloud_cos import CosConfig, CosS3Client

        required = ["COS_BUCKET", "COS_REGION", "COS_SECRET_ID", "COS_SECRET_KEY"]
        if any(not os.environ.get(key) for key in required):
            raise RuntimeError("请配置 COS_BUCKET、COS_REGION、COS_SECRET_ID、COS_SECRET_KEY")
        self.bucket = os.environ["COS_BUCKET"]
        self.session_token = os.getenv("COS_SESSION_TOKEN")
        self.prefix = os.getenv("COS_PREFIX", "pixelle").strip("/")
        self.client = CosS3Client(
            CosConfig(
                Region=os.environ["COS_REGION"],
                SecretId=os.environ["COS_SECRET_ID"],
                SecretKey=os.environ["COS_SECRET_KEY"],
                Token=os.getenv("COS_SESSION_TOKEN"),
                Scheme="https",
                Timeout=60,
            )
        )

    def key(self, value):
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value:
            raise ValueError("Invalid object key")
        return f"{self.prefix}/{value}" if self.prefix else value

    async def upload(self, key, path, content_type=None):
        await asyncio.to_thread(
            self.client.upload_file,
            Bucket=self.bucket,
            Key=self.key(key),
            LocalFilePath=str(path),
            PartSize=8,
            MAXThread=3,
            ACL="private",
            ContentType=content_type
            or mimetypes.guess_type(str(path))[0]
            or "application/octet-stream",
        )
        uploaded = await self.head(key)
        if int(uploaded.get("Content-Length", -1)) != Path(path).stat().st_size:
            raise RuntimeError("COS 上传大小校验失败")
        return key

    async def put_json(self, key, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket,
            Key=self.key(key),
            Body=body,
            ContentType="application/json",
            ACL="private",
        )
        return key

    async def get_json(self, key):
        def read():
            result = self.client.get_object(Bucket=self.bucket, Key=self.key(key))
            stream = result["Body"].get_raw_stream()
            try:
                return json.loads(stream.read())
            finally:
                stream.close()

        return await asyncio.to_thread(read)

    async def download(self, key, path, max_bytes=None):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        if max_bytes is not None:

            def bounded_download():
                result = self.client.get_object(Bucket=self.bucket, Key=self.key(key))
                stream = result["Body"].get_raw_stream()
                try:
                    total = 0
                    with Path(path).open("wb") as output:
                        while chunk := stream.read(min(64 * 1024, max_bytes - total + 1)):
                            total += len(chunk)
                            if total > max_bytes:
                                raise ValueError("文件大小超过上传申请")
                            output.write(chunk)
                finally:
                    stream.close()

            await asyncio.to_thread(bounded_download)
            return str(path)
        await asyncio.to_thread(
            self.client.download_file, Bucket=self.bucket, Key=self.key(key), DestFilePath=str(path)
        )
        return str(path)

    async def head(self, key):
        return await asyncio.to_thread(
            self.client.head_object, Bucket=self.bucket, Key=self.key(key)
        )

    def signed_url(self, key, method="GET", seconds=900, download_name=None):
        params = (
            {
                "response-content-disposition": "attachment; filename*=UTF-8''"
                + __import__("urllib.parse", fromlist=["quote"]).quote(download_name)
            }
            if download_name
            else {}
        )
        if self.session_token:
            params["x-cos-security-token"] = self.session_token
        return self.client.get_presigned_url(
            Method=method,
            Bucket=self.bucket,
            Key=self.key(key),
            Expired=seconds,
            Params=params,
            Headers={"x-cos-acl": "private"} if method.upper() == "PUT" else {},
        )

    async def delete(self, key):
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=self.key(key))

    async def delete_prefix(self, prefix):
        full_prefix = self.key(prefix.rstrip("/") + "/")
        marker = ""
        while True:
            result = await asyncio.to_thread(
                self.client.list_objects,
                Bucket=self.bucket,
                Prefix=full_prefix,
                Marker=marker,
                MaxKeys=1000,
            )
            objects = result.get("Contents", [])
            if objects:
                deleted = await asyncio.to_thread(
                    self.client.delete_objects,
                    Bucket=self.bucket,
                    Delete={"Object": [{"Key": item["Key"]} for item in objects], "Quiet": "false"},
                )
                if deleted.get("Error"):
                    raise RuntimeError("COS 删除部分失败，请重试删除任务")
            if str(result.get("IsTruncated", "false")).lower() != "true":
                break
            marker = result["NextMarker"]
