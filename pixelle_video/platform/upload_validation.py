"""Inspect uploaded bytes before making a media resource available."""

import json
import subprocess
import warnings
from pathlib import Path

from PIL import Image, UnidentifiedImageError


def validate_upload(path: Path, content_type: str, expected_size: int):
    if path.stat().st_size != expected_size:
        raise ValueError("文件大小与上传申请不一致")
    if content_type.startswith("image/"):
        expected_format = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}[
            content_type
        ]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    if image.format != expected_format:
                        raise ValueError("图片实际格式与上传申请不一致")
                    if image.width * image.height > 40_000_000:
                        raise ValueError("图片像素过大，请缩小后上传")
                    image.verify()
                with Image.open(path) as image:
                    image.load()
        except (
            UnidentifiedImageError,
            OSError,
            SyntaxError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as error:
            raise ValueError("文件不是有效的 PNG、JPEG 或 WebP 图片") from error
        return

    try:
        # Restrict the demuxer as well as protocols: a playlist disguised as audio
        # must never make the probe fetch URLs or other files.
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-f",
                "mp3" if content_type == "audio/mpeg" else "wav",
                "-show_entries",
                "stream=codec_type,codec_name:format=format_name,duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            timeout=15,
            check=True,
        )
        info = json.loads(result.stdout)
        streams = info.get("streams", [])
        audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
        expected_format = "mp3" if content_type == "audio/mpeg" else "wav"
        if (
            not audio
            or len(audio) != len(streams)
            or info.get("format", {}).get("format_name") != expected_format
            or (expected_format == "mp3" and any(s.get("codec_name") != "mp3" for s in audio))
        ):
            raise ValueError("音频实际格式与上传申请不一致")
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as error:
        raise ValueError("文件不是有效的 MP3 或 WAV 音频") from error
