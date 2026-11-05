import io
import subprocess
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from api.app import create_app
from pixelle_video.services.frame_html import HTMLFrameGenerator
from tests.platform.platform_test import platform as base_platform


@pytest.fixture
def platform(tmp_path, monkeypatch):
    yield from base_platform.__wrapped__(tmp_path, monkeypatch)


def login(client):
    assert (
        client.post(
            "/api/auth/login", json={"username": "creator", "password": "test-password"}
        ).status_code
        == 200
    )


def pending_upload(client, p, data, content_type, monkeypatch):
    response = client.post(
        "/api/uploads",
        json={
            "kind": "image" if content_type.startswith("image/") else "bgm",
            "name": "上传文件",
            "size": len(data),
            "content_type": content_type,
        },
    )
    assert response.status_code == 200
    asset_id = response.json()["id"]
    key = p.database.one("SELECT object_key FROM assets WHERE id=?", (asset_id,))["object_key"]
    p.storage.objects[key] = data

    async def head(key):
        return {"Content-Length": len(p.storage.objects[key]), "Content-Type": content_type}

    monkeypatch.setattr(p.storage, "head", head)
    return asset_id, key


def test_security_headers_cover_success_and_rejected_requests(platform, monkeypatch):
    monkeypatch.setenv("PIXELLE_COOKIE_SECURE", "true")
    with TestClient(create_app(platform), base_url="https://testserver") as client:
        for response in (
            client.get("/health"),
            client.get("/api/works"),
            client.post("/api/auth/login", headers={"Origin": "https://evil.test"}, json={}),
        ):
            policy = response.headers["Content-Security-Policy"]
            assert "script-src 'self';" in policy
            assert "frame-ancestors 'none'" in policy
            assert "object-src 'none'" in policy
            assert response.headers["X-Frame-Options"] == "DENY"
            assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
        assert client.get("/api/works").headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "content_type", ["image/png", "image/jpeg", "image/webp", "audio/mpeg", "audio/wav"]
)
def test_disguised_html_never_becomes_available(platform, monkeypatch, content_type):
    with TestClient(create_app(platform)) as client:
        login(client)
        asset_id, _ = pending_upload(
            client,
            platform,
            b'<svg onload="alert(1)"><script>alert(1)</script></svg>',
            content_type,
            monkeypatch,
        )
        assert client.post(f"/api/uploads/{asset_id}/confirm").status_code == 400
        assert (
            platform.database.one("SELECT confirmed FROM assets WHERE id=?", (asset_id,))[
                "confirmed"
            ]
            == 0
        )
        assert client.get(f"/api/resources/{asset_id}/url").status_code == 400


@pytest.mark.parametrize(
    "image_format,content_type",
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")],
)
def test_checked_images_cannot_be_replaced_using_pending_put(
    platform, monkeypatch, image_format, content_type
):
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(buffer, image_format)
    data = buffer.getvalue()
    with TestClient(create_app(platform)) as client:
        login(client)
        asset_id, pending = pending_upload(client, platform, data, content_type, monkeypatch)
        assert client.post(f"/api/uploads/{asset_id}/confirm").status_code == 200
        published = platform.resources.get(asset_id)["object_key"]
        assert published != pending
        platform.storage.objects[pending] = b"<script>alert(1)</script>"
        assert platform.storage.objects[published] == data
        assert client.post(f"/api/uploads/{asset_id}/confirm").status_code == 200
        assert platform.resources.get(asset_id)["object_key"] == published


@pytest.mark.parametrize("content_type", ["audio/wav", "audio/mpeg"])
def test_real_audio_uploads_are_accepted(platform, monkeypatch, tmp_path, content_type):
    path = tmp_path / "sample.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 8000)
    if content_type == "audio/mpeg":
        mp3 = tmp_path / "sample.mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), str(mp3)], check=True, timeout=15)
        path = mp3
    with TestClient(create_app(platform)) as client:
        login(client)
        asset_id, _ = pending_upload(client, platform, path.read_bytes(), content_type, monkeypatch)
        assert client.post(f"/api/uploads/{asset_id}/confirm").status_code == 200


def test_upload_changed_between_head_and_download_stays_unconfirmed(platform, monkeypatch):
    with TestClient(create_app(platform)) as client:
        login(client)
        asset_id, _ = pending_upload(client, platform, b"small", "image/png", monkeypatch)

        async def head(key):
            platform.storage.objects[key] = b"x" * 100
            return {"Content-Length": 5, "Content-Type": "image/png"}

        monkeypatch.setattr(platform.storage, "head", head)
        assert client.post(f"/api/uploads/{asset_id}/confirm").status_code == 400
        assert (
            platform.database.one("SELECT confirmed FROM assets WHERE id=?", (asset_id,))[
                "confirmed"
            ]
            == 0
        )


@pytest.mark.parametrize(
    "values",
    [
        {"author": {"html": "<script>alert(1)</script>"}},
        {"accent": "red; background: url(https://evil.test)"},
        {"size": "1; background: url(https://evil.test)"},
        {"size": float("nan")},
        {"size": True},
    ],
)
def test_template_parameters_reject_markup_and_css_injection(tmp_path, values):
    path = tmp_path / "320x480" / "template.html"
    path.parent.mkdir()
    path.write_text("{{author:text=作者}} {{accent:color=#fff}} {{size:number=72}}")
    generator = HTMLFrameGenerator(str(path))
    with pytest.raises(ValueError):
        generator.validate_parameter_values(values)


async def test_template_render_escapes_non_string_preset_values(tmp_path, monkeypatch):
    path = tmp_path / "320x480" / "template.html"
    path.parent.mkdir()
    path.write_text("<p>{{title}}</p>")
    captured = []

    class Page:
        async def goto(self, url, **kwargs):
            from urllib.parse import unquote, urlsplit

            captured.append(Path(unquote(urlsplit(url).path)).read_text())

        async def screenshot(self, **kwargs):
            pass

        async def close(self):
            pass

    class Browser:
        async def new_page(self, **kwargs):
            return Page()

    generator = HTMLFrameGenerator(str(path))

    async def browser():
        return Browser()

    monkeypatch.setattr(generator, "_ensure_browser", browser)
    await generator.generate_frame(
        "title",
        "text",
        "",
        ext={"title": {"payload": "<script>alert(1)</script>"}},
        output_path=str(tmp_path / "out.png"),
    )
    assert "<script>" not in captured[0]
    assert "&lt;script&gt;" in captured[0]
