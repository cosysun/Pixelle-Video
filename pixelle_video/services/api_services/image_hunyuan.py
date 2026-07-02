"""
腾讯混元生图 (HY-Image) API 客户端
腾讯云 TokenHub - hy-image-v3.0 (异步) / hy-image-lite (同步) 模型

文档: https://cloud.tencent.com/document/product/1823/130080

接口要点（base_url 为完整路径，非 OpenAI 风格 /v1/images）:
  - HY-Image 3.0 (异步两步):
      提交 POST /v1/api/image/submit -> {"id", "status": "queued"}
      查询 POST /v1/api/image/query  -> 轮询至 status == "completed"，取 data[].url
  - HY-Image Lite (同步):
      POST /v1/api/image/lite -> 直接返回 data[].url
鉴权: Authorization: Bearer <api_key>
images 仅支持可访问的 http(s) 图片地址，本地参考图会被跳过并告警。
"""

import logging
import os
import time
from typing import List, Optional

import httpx

try:
    from .config import Config
except ImportError:
    from config import Config

DEFAULT_BASE_URL = "https://tokenhub.tencentmaas.com"

# 混元 API resolution 格式 "宽:高"，见腾讯云混元生图文档
_RATIO_TO_RESOLUTION = {
    "9:16": "720:1280",
    "16:9": "1280:720",
    "1:1": "1024:1024",
    "3:4": "768:1024",
    "4:3": "1024:768",
    "3:5": "768:1280",
    "5:3": "1280:768",
}

# 文生图常用分桶（用于从任意 W*H snap 到最近支持尺寸）
_T2I_RESOLUTIONS = [
    (720, 1280),
    (768, 1280),
    (768, 1024),
    (1024, 768),
    (1024, 1024),
    (1280, 720),
    (768, 768),
]


class HunyuanImageClient:
    """腾讯混元生图客户端（TokenHub）。

    公共方法 ``generate_image`` 的签名与其它 image provider 客户端保持一致，
    由 ``ImageClient`` 统一调度。
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        local_proxy: Optional[str] = None,
        timeout: int = 300,
    ) -> None:
        self.api_key = (api_key or Config.HUNYUAN_API_KEY or os.getenv("HUNYUAN_API_KEY") or "").strip()
        self.base_url = (base_url or Config.HUNYUAN_BASE_URL or DEFAULT_BASE_URL).rstrip("/")
        self.local_proxy = local_proxy
        self.timeout = timeout
        # 异步任务轮询参数
        self.max_attempts = 30
        self.poll_interval = 5

    def _client(self) -> httpx.Client:
        client_kwargs: dict = {"timeout": self.timeout}
        if self.local_proxy:
            client_kwargs["proxy"] = self.local_proxy
        return httpx.Client(**client_kwargs)

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _prepare_images(self, image_paths: Optional[List[str]]) -> List[str]:
        """混元 images 仅支持可访问的 http(s) 地址，本地路径跳过并告警。"""
        if not image_paths:
            return []
        urls = []
        for p in image_paths:
            if isinstance(p, str) and (p.startswith("http://") or p.startswith("https://")):
                urls.append(p)
            else:
                logging.warning(
                    "HunyuanImageClient only supports accessible http(s) reference images; "
                    f"skipping local/unsupported path: {p}"
                )
        return urls

    @staticmethod
    def _parse_size(size: Optional[str]) -> Optional[tuple[int, int]]:
        if not size:
            return None
        parts = size.replace("x", "*").split("*")
        if len(parts) != 2:
            return None
        try:
            w, h = int(parts[0]), int(parts[1])
            if w > 0 and h > 0:
                return w, h
        except ValueError:
            pass
        return None

    @classmethod
    def _snap_to_nearest(cls, width: int, height: int, candidates: list[tuple[int, int]]) -> str:
        target_ratio = width / height
        best = min(
            candidates,
            key=lambda wh: abs(wh[0] / wh[1] - target_ratio),
        )
        return f"{best[0]}:{best[1]}"

    @classmethod
    def _resolve_resolution(
        cls,
        video_ratio: Optional[str] = None,
        size: Optional[str] = None,
        has_reference: bool = False,
    ) -> str:
        """Map Pixelle size/ratio to Hunyuan ``resolution`` string."""
        if has_reference:
            candidates = [(768, 768), (768, 1024), (1024, 768), (1024, 1024)]
            parsed = cls._parse_size(size)
            if parsed:
                return cls._snap_to_nearest(parsed[0], parsed[1], candidates)
            if video_ratio and video_ratio in _RATIO_TO_RESOLUTION:
                ref_w, ref_h = map(int, _RATIO_TO_RESOLUTION[video_ratio].split(":"))
                return cls._snap_to_nearest(ref_w, ref_h, candidates)
            return "1024:1024"

        parsed = cls._parse_size(size)
        if parsed:
            return cls._snap_to_nearest(parsed[0], parsed[1], _T2I_RESOLUTIONS)

        if video_ratio and video_ratio in _RATIO_TO_RESOLUTION:
            return _RATIO_TO_RESOLUTION[video_ratio]

        return "1024:1024"

    def generate_image(
        self,
        prompt: str,
        model: str = "hy-image-v3.0",
        save_dir: Optional[str] = None,
        session_id: Optional[str] = None,
        video_ratio: Optional[str] = None,
        resolution: Optional[str] = None,
        size: Optional[str] = None,
        image_paths: Optional[List[str]] = None,
    ) -> List[str]:
        if not self.api_key:
            raise RuntimeError("HUNYUAN_API_KEY is not configured")
        if not save_dir:
            raise RuntimeError("save_dir is required for Hunyuan image generation")

        os.makedirs(save_dir, exist_ok=True)
        image_urls = self._prepare_images(image_paths)
        hunyuan_resolution = self._resolve_resolution(
            video_ratio=video_ratio,
            size=size,
            has_reference=bool(image_urls),
        )
        logging.info(f"Hunyuan resolution: {hunyuan_resolution} (ratio={video_ratio}, size={size})")

        is_lite = "lite" in model.lower()
        if is_lite:
            urls = self._generate_lite(prompt, model, hunyuan_resolution)
        else:
            urls = self._generate_v3(prompt, model, image_urls, hunyuan_resolution)

        generated_paths = []
        for idx, url in enumerate(urls):
            local_path = self._download_image(url, save_dir, idx)
            if local_path:
                generated_paths.append(local_path)
        return generated_paths

    def _generate_lite(self, prompt: str, model: str, resolution: str) -> List[str]:
        """HY-Image Lite 同步生成。"""
        payload = {
            "model": model,
            "prompt": prompt,
            "resolution": resolution,
            "rsp_img_type": "url",
            # logo_add: 0 关闭水印 (原 API 参数 LogoAdd, 默认 1 会加水印)
            "logo_add": 0,
        }
        with self._client() as client:
            resp = client.post(
                f"{self.base_url}/v1/api/image/lite",
                headers=self._headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return self._extract_urls(data)

    def _generate_v3(
        self,
        prompt: str,
        model: str,
        image_urls: List[str],
        resolution: str,
    ) -> List[str]:
        """HY-Image 3.0 异步生成：提交任务后轮询查询。"""
        submit_payload: dict = {
            "model": model,
            "prompt": prompt,
            "resolution": resolution,
            # logo_add: 0 关闭水印 (原 API 参数 LogoAdd, 默认 1 会加水印)
            "logo_add": 0,
        }
        if image_urls:
            submit_payload["images"] = image_urls

        with self._client() as client:
            resp = client.post(
                f"{self.base_url}/v1/api/image/submit",
                headers=self._headers,
                json=submit_payload,
            )
            resp.raise_for_status()
            submit_data = resp.json()
            job_id = submit_data.get("id")
            if not job_id:
                raise RuntimeError(f"Hunyuan submit did not return a job id: {submit_data}")

            query_payload = {"model": model, "id": job_id}
            for attempt in range(self.max_attempts):
                query_resp = client.post(
                    f"{self.base_url}/v1/api/image/query",
                    headers=self._headers,
                    json=query_payload,
                )
                query_resp.raise_for_status()
                query_data = query_resp.json()
                status = (query_data.get("status") or "").lower()

                if status == "completed":
                    return self._extract_urls(query_data)
                if status in ("failed", "error", "cancelled"):
                    raise RuntimeError(f"Hunyuan job {job_id} failed: {query_data}")

                if attempt + 1 < self.max_attempts:
                    time.sleep(self.poll_interval)

        raise RuntimeError(
            f"Hunyuan job {job_id} not completed after {self.max_attempts} polls"
        )

    @staticmethod
    def _extract_urls(data: dict) -> List[str]:
        items = data.get("data") or []
        urls = [item.get("url") for item in items if isinstance(item, dict) and item.get("url")]
        if not urls:
            raise RuntimeError(f"Hunyuan response did not contain image url: {data}")
        return urls

    def _download_image(self, url: str, save_dir: str, idx: int) -> Optional[str]:
        file_name = f"hunyuan_{int(time.time())}_{idx}.png"
        file_path = os.path.join(save_dir, file_name)
        try:
            with self._client() as client:
                resp = client.get(url)
                resp.raise_for_status()
                with open(file_path, "wb") as f:
                    f.write(resp.content)
            return file_path
        except Exception as e:
            logging.error(f"Failed to download Hunyuan image from {url}: {e}")
            return None


if __name__ == "__main__":
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    api_key = Config.HUNYUAN_API_KEY
    if not api_key:
        print("HUNYUAN_API_KEY 未设置，跳过")
        sys.exit(1)

    save_dir = "code/result/image/test_avail"
    os.makedirs(save_dir, exist_ok=True)
    client = HunyuanImageClient(api_key=api_key, local_proxy=Config.LOCAL_PROXY or None)

    prompt = "雨中, 竹林, 小路, 水墨风格"

    print(f"[测试1: HY-Image 3.0] {prompt}")
    t0 = time.time()
    try:
        client.max_attempts = 30
        paths = client.generate_image(prompt=prompt, model="hy-image-v3.0", save_dir=save_dir)
        print(f"✓ 生成 {len(paths)} 张 ({time.time() - t0:.1f}s): {paths}")
    except Exception as e:
        print(f"✗ 失败 ({time.time() - t0:.1f}s): {e}")

    print(f"\n[测试2: HY-Image Lite] {prompt}")
    t0 = time.time()
    try:
        paths = client.generate_image(prompt=prompt, model="hy-image-lite", save_dir=save_dir)
        print(f"✓ 生成 {len(paths)} 张 ({time.time() - t0:.1f}s): {paths}")
    except Exception as e:
        print(f"✗ 失败 ({time.time() - t0:.1f}s): {e}")
