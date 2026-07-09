"""Google Gemini native image generation client (interactions API via google-genai SDK).

The Gemini API moved image generation off the legacy
``POST /v1/models/{model}:generateContent`` endpoint in May 2026 (see
https://ai.google.dev/gemini-api/docs/interactions-breaking-changes-may-2026).
Image output configuration ``image_config`` was removed from ``generation_config``
and now lives under ``response_format`` on the new
``POST /v1beta/interactions`` endpoint.

Rather than hand-rolling the new schema, this client uses Google's official
``google-genai`` SDK, which abstracts both URL and body shape and survives
future breaking changes more gracefully.
"""

import base64
import os
import time
import uuid
from typing import List, Optional, Union

from google import genai
from google.genai import types

try:
    from .config import Config
except ImportError:
    from config import Config

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
MAX_REFERENCE_IMAGES = 14

RESOLUTION_TO_IMAGE_SIZE = {
    "720P": "512",
    "1080P": "1K",
    "2K": "2K",
    "4K": "4K",
}


class GeminiImageClient:
    """Gemini image generation via the interactions API.

    Public surface is unchanged from the previous httpx-based implementation
    so callers (``ImageClient`` in ``image_client.py``) need no updates.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        local_proxy: Optional[str] = None,
        timeout: float = 300.0,
    ):
        self.api_key = api_key or Config.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", "")
        self.base_url = (base_url or Config.GOOGLE_GEMINI_BASE_URL or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.local_proxy = local_proxy
        self.max_attempts = 10

    def _create_client(self) -> "genai.Client":
        """Build a configured google-genai client.

        Honors:
          - api_key
          - base_url (only when not the public default)
          - timeout (passed to HttpOptions in milliseconds)
          - local_proxy (forwarded to the underlying ``httpx.Client`` via
            ``HttpOptions.client_args``)
        """
        http_kwargs: dict = {"timeout": int(self.timeout * 1000)}
        if self.base_url and self.base_url != DEFAULT_BASE_URL:
            http_kwargs["base_url"] = self.base_url
        if self.local_proxy:
            # google-genai forwards ``client_args`` straight into ``httpx.Client(**client_args)``.
            http_kwargs["client_args"] = {"proxy": self.local_proxy}

        http_options = types.HttpOptions(**http_kwargs)
        return genai.Client(api_key=self.api_key, http_options=http_options)

    def _mime_type(self, image_path: str) -> str:
        ext = os.path.splitext(image_path)[1].lower()
        mime_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".heic": "image/heic",
            ".heif": "image/heif",
        }
        return mime_types.get(ext, "image/jpeg")

    def _build_image_part(self, image_path: str) -> dict:
        """Encode a reference image into an interactions ``input`` image part."""
        if image_path.startswith("data:"):
            header, _, data = image_path.partition(",")
            mime_type = header.split(";")[0].replace("data:", "")
            return {"type": "image", "mime_type": mime_type, "data": data}

        abs_path = os.path.abspath(image_path)
        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"Reference image not found: {image_path}")

        with open(abs_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        return {"type": "image", "mime_type": self._mime_type(abs_path), "data": encoded}

    def _map_image_size(self, resolution: Optional[str], model: str = "") -> str:
        # gemini-3.1-flash-lite-image only supports 1K output (see Gemini image-gen docs).
        if "flash-lite" in model.lower():
            return "1K"
        if not resolution:
            return "1K"
        return RESOLUTION_TO_IMAGE_SIZE.get(resolution.upper(), "2K")

    @staticmethod
    def _read_attr(obj, key: str):
        """Read ``key`` from an object (attr) or dict (item), returning ``None`` if absent."""
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    def _extract_image_bytes(self, interaction) -> bytes:
        """Pull image bytes out of an SDK Interaction response.

        Preferred: ``interaction.output_image.data`` (the SDK's convenience accessor
        for the last image block).
        Fallback: walk ``interaction.steps[].content[]`` for ``type == "image"`` blocks.
        """
        output_image = self._read_attr(interaction, "output_image")
        data = self._read_attr(output_image, "data")
        if data:
            return base64.b64decode(data)

        steps = self._read_attr(interaction, "steps") or []
        for step in steps:
            if self._read_attr(step, "type") != "model_output":
                continue
            for block in self._read_attr(step, "content") or []:
                if self._read_attr(block, "type") == "image":
                    data = self._read_attr(block, "data")
                    if data:
                        return base64.b64decode(data)

        raise RuntimeError("Gemini interactions response did not contain image data")

    def generate_image(
        self,
        prompt: str,
        model: str = "gemini-3.1-flash-image",
        save_dir: Optional[str] = None,
        aspect_ratio: Optional[str] = "16:9",
        resolution: Optional[str] = "1K",
        image_paths: Optional[List[str]] = None,
    ) -> str:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        if not save_dir:
            raise RuntimeError("save_dir is required for Gemini image generation")

        # Build ``input`` - plain string for text-only, typed-parts array when refs are present
        input_payload: Union[str, list[dict]]
        if image_paths:
            parts: list[dict] = [{"type": "text", "text": prompt}]
            for path in image_paths[:MAX_REFERENCE_IMAGES]:
                parts.append(self._build_image_part(path))
            input_payload = parts
        else:
            input_payload = prompt

        response_format = {
            "type": "image",
            "mime_type": "image/jpeg",
            "aspect_ratio": aspect_ratio or "16:9",
            "image_size": self._map_image_size(resolution, model),
        }

        client = self._create_client()
        last_error: Optional[Exception] = None
        for attempt in range(self.max_attempts):
            try:
                interaction = client.interactions.create(
                    model=model,
                    input=input_payload,
                    response_format=response_format,
                )
                image_bytes = self._extract_image_bytes(interaction)

                os.makedirs(save_dir, exist_ok=True)
                file_name = f"gemini_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
                file_path = os.path.join(save_dir, file_name)
                with open(file_path, "wb") as f:
                    f.write(image_bytes)
                return file_path
            except Exception as exc:
                last_error = exc
                if attempt + 1 >= self.max_attempts:
                    break
                time.sleep(10)

        raise RuntimeError(
            f"Gemini image generation failed after {self.max_attempts} attempts: {last_error}"
        ) from last_error


if __name__ == "__main__":
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    api_key = Config.GEMINI_API_KEY
    if not api_key:
        print("GEMINI_API_KEY not set, skipping")
        sys.exit(1)

    save_dir = "code/result/image/test_avail"
    os.makedirs(save_dir, exist_ok=True)
    client = GeminiImageClient(api_key=api_key, local_proxy=Config.LOCAL_PROXY or None)
    client.max_attempts = 1

    prompt = "A cute orange cat lying on a sunny windowsill, watercolor style"
    print(f"Testing gemini-3.1-flash-image: {prompt[:60]}...")
    t0 = time.time()
    try:
        path = client.generate_image(
            prompt=prompt,
            model="gemini-3.1-flash-image",
            save_dir=save_dir,
            aspect_ratio="16:9",
            resolution="1080P",
        )
        print(f"Success ({time.time() - t0:.1f}s): {path}")
    except Exception as exc:
        print(f"Failed ({time.time() - t0:.1f}s): {exc}")
