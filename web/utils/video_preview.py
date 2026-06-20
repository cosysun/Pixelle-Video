"""Helpers for rendering local videos without stale path-based caching."""


def read_video_preview_bytes(video_path: str) -> bytes:
    """Read the latest bytes for a local video preview."""
    with open(video_path, "rb") as video_file:
        return video_file.read()
