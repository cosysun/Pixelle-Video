"""OpenRouter unified image API client."""

import base64
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import List, Optional

import httpx

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterImageClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        local_proxy: Optional[str] = None,
        timeout: float = 300.0,
    ) -> None:
        self.api_key = (api_key or os.getenv("OPENROUTER_API_KEY") or "").strip()
        self.base_url = (base_url or os.getenv("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.local_proxy = local_proxy
        self.timeout = timeout

    def _client(self) -> httpx.Client:
        kwargs = {"timeout": self.timeout}
        if self.local_proxy:
            kwargs["proxy"] = self.local_proxy
        return httpx.Client(**kwargs)

    @staticmethod
    def _reference(path: str) -> str:
        if path.startswith(("http://", "https://", "data:")):
            return path

        file_path = Path(path)
        if not file_path.is_file():
            raise ValueError(f"OpenRouter reference image not found: {path}")
        media_type = mimetypes.guess_type(file_path.name)[0] or "image/png"
        encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
        return f"data:{media_type};base64,{encoded}"

    def generate_image(
        self,
        prompt: str,
        model: str,
        save_dir: str,
        video_ratio: str = "16:9",
        resolution: str = "2K",
        image_paths: Optional[List[str]] = None,
    ) -> List[str]:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")

        payload = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "aspect_ratio": video_ratio,
            "resolution": "1K" if resolution in {"720P", "1080P"} else resolution,
            "output_format": "png",
        }
        if image_paths:
            payload["input_references"] = [
                {"type": "image_url", "image_url": {"url": self._reference(path)}}
                for path in image_paths
            ]

        with self._client() as client:
            response = client.post(
                f"{self.base_url}/images",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            result = response.json()

        images = result.get("data") or []
        if not images:
            raise RuntimeError(f"OpenRouter image API returned no images: {result}")

        os.makedirs(save_dir, exist_ok=True)
        paths = []
        for image in images:
            encoded = image.get("b64_json")
            if not encoded:
                raise RuntimeError(f"OpenRouter image response missing b64_json: {image}")
            extension = ".svg" if image.get("media_type") == "image/svg+xml" else ".png"
            path = os.path.join(
                save_dir,
                f"openrouter_{int(time.time())}_{uuid.uuid4().hex[:6]}{extension}",
            )
            with open(path, "wb") as file:
                file.write(base64.b64decode(encoded, validate=True))
            paths.append(path)
        return paths
