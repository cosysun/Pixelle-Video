import asyncio
import json
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.schemas.studio import EditRequest
from pixelle_video.config import config_manager
from pixelle_video.config.schema import PixelleVideoConfig
from pixelle_video.models.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from pixelle_video.platform.cli import import_history, import_resources
from pixelle_video.platform.database import Database, now
from pixelle_video.platform.jobs import JobManager
from pixelle_video.platform.runtime import Platform
from pixelle_video.services.task_editor import TaskEditService
from pixelle_video.utils.os_util import temporary_workspace
from tests.platform.fakes import MemoryStorage


@pytest.fixture
def platform(tmp_path, monkeypatch):
    original = config_manager.config
    original_db = config_manager.database
    config_manager.config = PixelleVideoConfig()
    monkeypatch.setenv("PIXELLE_USERNAME", "creator")
    monkeypatch.setenv("PIXELLE_PASSWORD", "test-password")
    monkeypatch.setenv("PIXELLE_COOKIE_SECURE", "false")
    monkeypatch.delenv("PIXELLE_PUBLIC_URL", raising=False)
    monkeypatch.delenv("PIXELLE_TRUSTED_PROXIES", raising=False)
    p = Platform(Database(tmp_path / "platform.sqlite3"), MemoryStorage())
    p.resources.builtin_templates = {}
    yield p
    p.database.close()
    config_manager.config = original
    config_manager.database = original_db


async def seed(platform, work_id="work1", completed=True):
    with (
        tempfile.TemporaryDirectory() as directory,
        temporary_workspace(directory),
        platform.persistence.operation(work_id),
    ):
        audio = Path(directory) / "voice.mp3"
        audio.write_bytes(b"audio")
        video = Path(directory) / "segment.mp4"
        video.write_bytes(b"segment")
        final = Path(directory) / "final.mp4"
        final.write_bytes(b"final")
        frames = [
            StoryboardFrame(
                index=i,
                narration=f"旁白 {i}",
                image_prompt=None,
                audio_path=str(audio),
                video_segment_path=str(video) if i == 0 or completed else None,
                duration=1,
            )
            for i in range(3)
        ]
        sb = Storyboard(
            title="一个好故事",
            config=StoryboardConfig(
                task_id=work_id,
                media_width=320,
                media_height=480,
                frame_template="1080x1920/static_default.html",
            ),
            frames=frames,
            final_video_path=str(final) if completed else None,
        )
        metadata = {
            "task_id": work_id,
            "created_at": now(),
            "status": "completed" if completed else "failed",
            "input": {
                "text": "灵感",
                "frame_template": sb.config.frame_template,
                "api_key": "secret",
                "progress_callback": lambda: None,
            },
            "result": {"video_path": str(final)} if completed else {},
        }
        await platform.persistence.save_storyboard(work_id, sb)
        await platform.persistence.save_task_metadata(work_id, metadata)
    return await platform.persistence._manifest(work_id)


async def test_cos_manifest_restores_without_local_outputs(platform):
    manifest = await seed(platform)
    assert manifest["metadata"]["input"]["api_key"] == ""
    assert "progress_callback" not in manifest["metadata"]["input"]
    assert manifest["storyboard"]["frames"][0]["audio_path"].startswith("cos://")
    assert len(platform.storage.uploads) == len(set(platform.storage.uploads))
    with (
        tempfile.TemporaryDirectory() as directory,
        temporary_workspace(directory),
        platform.persistence.operation("work1", editing=True),
    ):
        sb = await platform.persistence.load_storyboard("work1")
        assert Path(sb.frames[0].audio_path).read_bytes() == b"audio"
        assert str(directory) in sb.frames[0].audio_path
        previous = platform.persistence._row("work1")["manifest"]
        platform.storage.fail = True
        sb.title = "Should not publish"
        with pytest.raises(RuntimeError):
            await platform.persistence.save_storyboard("work1", sb)
        assert platform.persistence._row("work1")["manifest"] == previous
    assert (await platform.persistence._manifest("work1"))["storyboard"]["title"] == "一个好故事"


async def test_edit_commits_new_revision_and_reuses_segments(platform):
    await seed(platform)
    previous = platform.persistence._row("work1")["manifest"]

    class Video:
        def concat_videos(self, videos, output, **kwargs):
            assert output not in videos
            assert Path(output).parent.name == "work1"
            Path(output).write_bytes(b"new-final")

    with (
        tempfile.TemporaryDirectory() as directory,
        temporary_workspace(directory),
        platform.persistence.operation("work1", editing=True),
    ):
        editor = TaskEditService(SimpleNamespace(persistence=platform.persistence, video=Video()))
        await editor.remove_bgm("work1")
        await editor.delete_frame("work1", 1)
    current = await platform.persistence._manifest("work1")
    assert len(current["storyboard"]["frames"]) == 2
    assert current["metadata"]["status"] == "completed"
    assert platform.persistence._row("work1")["manifest"] != previous
    assert previous in platform.storage.objects
    assert all(f["audio_path"].startswith("cos://") for f in current["storyboard"]["frames"])


async def test_incomplete_edit_checkpoint_and_shared_frame_validation(platform):
    await seed(platform, completed=False)
    with (
        tempfile.TemporaryDirectory() as directory,
        temporary_workspace(directory),
        platform.persistence.operation("work1", editing=True),
    ):
        editor = TaskEditService(SimpleNamespace(persistence=platform.persistence))
        sb, metadata = await editor._load_task("work1")
        sb.frames[1].narration = "修改后旁白"
        await editor._checkpoint_incomplete_edit("work1", sb, metadata, ["2"])
        with pytest.raises(ValueError, match="之前"):
            await editor.insert_frame("work1", 0, "新分镜", None, image_source_index=0)
    assert (await platform.persistence._manifest("work1"))["storyboard"]["frames"][1][
        "narration"
    ] == "修改后旁白"


def test_auth_sessions_origin_redaction_and_validation(platform):
    with TestClient(create_app(platform)) as client:
        assert client.get("/api/works").status_code == 401
        assert (
            client.post(
                "/api/auth/login", json={"username": "creator", "password": "bad"}
            ).status_code
            == 401
        )
        response = client.post(
            "/api/auth/login", json={"username": "creator", "password": "test-password"}
        )
        assert response.status_code == 200
        assert "httponly" in response.headers["set-cookie"].lower()
        assert client.get("/api/auth/me").json()["username"] == "creator"
        assert (
            client.post(
                "/api/video/generate", json={}, headers={"Origin": "https://attacker.test"}
            ).status_code
            == 403
        )
        assert client.patch("/api/settings", json={"comfyui": {}}).status_code == 400
        assert client.patch("/api/settings", json={"llm": {"unknown": 1}}).status_code == 400
        assert (
            client.patch(
                "/api/settings", json={"llm": {"base_url": "file:///etc/passwd"}}
            ).status_code
            == 400
        )
        assert (
            client.patch("/api/settings", json={"llm": {"api_key": "supersecret"}}).status_code
            == 200
        )
        masked = client.get("/api/settings").json()
        assert masked["llm"]["api_key"] == "••••••••"
        assert (
            client.patch(
                "/api/settings", json={"llm": {"api_key": "••••••••", "model": "new"}}
            ).status_code
            == 200
        )
        assert config_manager.config.llm.api_key == "supersecret"
        assert (
            client.post(
                "/api/uploads",
                json={"kind": "bgm", "name": "x", "size": 3, "content_type": "image/png"},
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/uploads",
                json={
                    "kind": "image",
                    "name": "x",
                    "size": 101 * 1024**2,
                    "content_type": "image/png",
                },
            ).status_code
            == 422
        )
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/works").status_code == 401


def test_plain_password_unicode_and_error_redaction(platform, monkeypatch):
    from pixelle_video.platform.security import safe_error

    password = "中文$Password#2026"
    monkeypatch.setenv("PIXELLE_PASSWORD", password)
    with TestClient(create_app(platform)) as client:
        response = client.post(
            "/api/auth/login", json={"username": "creator", "password": password}
        )
        assert response.status_code == 200
        assert client.get("/api/auth/me").status_code == 200
        assert password not in response.text
        assert client.post(
            "/api/auth/login", json={"username": "creator", "password": password + "x"}
        ).status_code == 401
        assert password not in safe_error(ValueError("Rejected " + password))
        assert password not in json.dumps(
            platform.database.all("SELECT * FROM settings"), ensure_ascii=False
        )


def test_missing_plain_password_prevents_startup(platform, monkeypatch):
    monkeypatch.delenv("PIXELLE_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="PIXELLE_USERNAME 和 PIXELLE_PASSWORD"):
        with TestClient(create_app(platform)):
            pass


async def test_quick_video_adapter_after_retired_model_cleanup(platform, tmp_path, monkeypatch):
    from pixelle_video.services.api_media import APIProviderMediaService

    media = APIProviderMediaService(config_manager.config.to_dict())
    keys = {item["key"] for item in media.list_workflows()}
    assert "api/dashscope/wan2.7-i2v" in keys
    for retired in ("wan2.7-r2v", "wan2.7-videoedit", "happyhorse-1.0-r2v", "happyhorse-1.0-video-edit"):
        with pytest.raises(ValueError, match="not found"):
            media.resolve_workflow("api/dashscope/" + retired)
    image = tmp_path / "first.png"
    image.write_bytes(b"test-image")
    output = tmp_path / "result.mp4"
    captured = {}

    def generate_video(**kwargs):
        captured.update(kwargs)
        Path(kwargs["save_path"]).write_bytes(b"generated-video")

    monkeypatch.setattr(
        media, "_create_video_client", lambda: SimpleNamespace(generate_video=generate_video)
    )
    result = await media(
        prompt="风景",
        workflow="api/dashscope/wan2.7-i2v",
        image_path=str(image),
        output_path=str(output),
        width=720,
        height=1280,
        duration=5,
    )
    assert captured["image_path"] == str(image)
    assert captured["video_ratio"] == "9:16"
    assert captured["duration"] == 5
    assert not any(key.startswith("reference_") for key in captured)
    assert result.url == str(output)


def test_dashscope_proxy_submission_retains_first_frame_and_restores_environment(
    tmp_path, monkeypatch
):
    import os

    from pixelle_video.services.api_services import video_dashscope

    image = tmp_path / "first.png"
    image.write_bytes(b"test-image")
    output = tmp_path / "result.mp4"
    captured = {}
    monkeypatch.setenv("HTTP_PROXY", "http://original.test")

    def submit(**kwargs):
        captured.update(kwargs)
        assert os.environ["HTTP_PROXY"] == "http://proxy.test"
        return SimpleNamespace(status_code=200, output={"video_url": "https://result.test/video.mp4"})

    monkeypatch.setattr(video_dashscope, "VideoSynthesis", SimpleNamespace(call=submit))
    monkeypatch.setattr(
        video_dashscope.requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=200, iter_content=lambda chunk_size: [b"video"]
        ),
    )
    client = video_dashscope.DashscopeVideoClient(
        api_key="test-key", local_proxy="http://proxy.test"
    )
    client.generate_video("风景", str(image), str(output), duration=5)
    assert captured["media"] == [{"type": "first_frame", "url": image.as_uri()}]
    assert output.read_bytes() == b"video"
    assert os.environ["HTTP_PROXY"] == "http://original.test"


@pytest.mark.parametrize(
    ("origin", "status"),
    [
        ("https://studio.test", 200),
        ("https://studio.test:443", 200),
        ("https://STUDIO.test", 200),
        ("http://studio.test", 403),
        ("https://attacker.test", 403),
        ("https://studio.test:8443", 403),
        ("https://studio.test@attacker.test", 403),
        ("https://studio.test:not-a-port", 403),
        ("null", 403),
    ],
)
def test_https_origin_behind_internal_http_without_deployment_overrides(
    platform, monkeypatch, origin, status
):
    monkeypatch.delenv("PIXELLE_COOKIE_SECURE", raising=False)
    with TestClient(create_app(platform), base_url="http://studio.test") as client:
        response = client.post(
            "/api/auth/login",
            json={"username": "creator", "password": "test-password"},
            headers={"Origin": origin, "X-Forwarded-Proto": "http"},
        )
        assert response.status_code == status
        if status == 200:
            assert "secure" in response.headers["set-cookie"].lower()
            assert client.get("/api/auth/me").status_code == 401
            assert client.get("https://studio.test/api/auth/me").status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"action": "update_title", "values": {"title": "ok", "position": 0}},
        {"action": "delete_frame", "values": {"frame_index": -1}},
        {
            "action": "regenerate_frame_audio",
            "values": {"frame_index": 0, "narration": "x", "tts_overrides": {"tts_speed": 10}},
        },
        {
            "action": "regenerate_frame_media",
            "values": {
                "frame_index": 0,
                "image_prompt": "x",
                "media_overrides": {"media_workflow": "runninghub/x"},
            },
        },
        {"action": "update_title", "values": {}},
    ],
)
def test_edit_contract_rejects_invalid_input(body):
    with pytest.raises(ValueError):
        EditRequest(**body)


async def test_queue_extensible_cancel_failure_and_recovery(platform):
    calls = []

    async def article(job):
        calls.append(job["id"])
        return {"article": "正文"}

    manager = JobManager(platform.database)
    manager.register("article.generate", article)
    cancelled = manager.submit("article.generate", {}, work_id="a")
    manager.cancel(cancelled["id"])
    item = manager.submit("article.generate", {}, work_id="b")
    with pytest.raises(ValueError):
        manager.submit("article.generate", {}, work_id="b")
    await manager.start()
    for _ in range(100):
        if manager.get(item["id"])["status"] == "completed":
            break
        await asyncio.sleep(0.02)
    await manager.stop()
    assert manager.get(item["id"])["result"] == {"article": "正文"}
    assert cancelled["id"] not in calls
    platform.database.execute("UPDATE jobs SET status='running' WHERE id=?", (item["id"],))
    await manager.start()
    await manager.stop()
    assert manager.get(item["id"])["status"] == "interrupted"


async def test_resource_import_and_repeatable_history_migration(platform, tmp_path):
    source = tmp_path / "source"
    template = source / "templates/320x480/static_test.html"
    template.parent.mkdir(parents=True)
    template.write_text("<html><body>{{title}}{{text}}</body></html>")
    assert await import_resources(platform, source) == 1
    assert await import_resources(platform, source) == 1
    assert len(platform.resources.list("template")) == 1
    assert not platform.storage.uploads
    asset = platform.resources.list("template")[0]
    with tempfile.TemporaryDirectory() as directory, temporary_workspace(directory):
        params = await platform.resources.prepare_video_params({"template_id": asset["id"]})
        assert Path(params["frame_template"]).is_file()
        assert "320x480" in params["frame_template"]
    output = source / "output"
    legacy = output / "legacy1"
    legacy.mkdir(parents=True)
    (legacy / "final.mp4").write_bytes(b"old-video")
    (legacy / "metadata.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "input": {"text": "old"},
                "result": {"video_path": "output/legacy1/final.mp4"},
                "config": {"api_key": "old-secret"},
            }
        )
    )
    result = await import_history(platform, output)
    assert result["imported"] == ["legacy1"] and not result["failed"]
    assert (legacy / "final.mp4").read_bytes() == b"old-video"
    assert (await import_history(platform, output))["skipped"] == ["legacy1"]
    assert (await platform.persistence._manifest("legacy1"))["metadata"]["result"][
        "video_path"
    ].startswith("cos://")


async def test_real_static_generation_ffmpeg_html_and_cos(platform, tmp_path):
    """Uses real Chromium and FFmpeg, substitutes only paid/network provider calls."""
    from pixelle_video.service import PixelleVideoCore

    template = tmp_path / "320x480/static_test.html"
    template.parent.mkdir()
    template.write_text(
        "<html><style>body{background:#faf5ef;padding:20px;font-size:28px}</style><h1>{{title}}</h1><p>{{text}}</p></html>"
    )
    core = PixelleVideoCore(persistence=platform.persistence)
    await core.initialize()

    async def voice(**kwargs):
        path = Path(kwargs["output_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(24000)
            stream.writeframes(b"\0\0" * 12000)
        return str(path)

    core.tts = voice
    with (
        tempfile.TemporaryDirectory() as directory,
        temporary_workspace(directory),
        platform.persistence.operation("real1"),
    ):
        try:
            await core.generate_video(
                "第一段。\n\n第二段。\n\n第三段。",
                mode="fixed",
                title="真实合成验证",
                task_id="real1",
                frame_template=str(template),
                media_width=320,
                media_height=480,
                tts_inference_mode="local",
                tts_voice="zh-CN-XiaoxiaoNeural",
            )
        finally:
            await core.cleanup()
    manifest = await platform.persistence._manifest("real1")
    assert manifest["metadata"]["status"] == "completed"
    assert len(manifest["storyboard"]["frames"]) == 3
    assert manifest["metadata"]["result"]["file_size"] > 1000
    assert all(
        f["video_segment_path"].startswith("cos://") for f in manifest["storyboard"]["frames"]
    )


async def test_runtime_generation_full_history_edit_and_failure(platform, tmp_path, monkeypatch):
    from PIL import Image

    from pixelle_video.service import PixelleVideoCore

    # Keep every task under a test-only temp root and verify cleanup each time.
    temp_root = tmp_path / "processing"
    temp_root.mkdir()
    monkeypatch.setenv("PIXELLE_TEMP_DIR", str(temp_root))
    source = tmp_path / "resources"
    folder = source / "templates/320x480"
    folder.mkdir(parents=True)
    html = '<html><style>body{background:{{bg_color:color=#f6f2ed}};padding:10px;font-size:20px}img{width:280px;height:220px}</style><h1>{{title}}</h1><img src="{{image}}"><p>{{text}}</p></html>'
    (folder / "image_test.html").write_text(html)
    (folder / "image_second.html").write_text(html)
    await import_resources(platform, source)
    assets = platform.resources.list("template")
    assert assets[0]["metadata"]["parameters"]["bg_color"]["type"] == "color"

    async def voice(**kwargs):
        path = Path(kwargs["output_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(24000)
            stream.writeframes(b"\0\0" * 6000)
        return str(path)

    broken = False

    async def media(**kwargs):
        if broken:
            raise ValueError("provider rejected request")
        path = Path(kwargs["output_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (280, 220), (157, 173, 139)).save(path)
        from pixelle_video.models.media import MediaResult

        return MediaResult(url=str(path), media_type="image")

    async def core_factory():
        core = PixelleVideoCore(persistence=platform.persistence)
        await core.initialize()
        core.tts = voice
        core.media = media
        return core

    platform.video_core = core_factory
    # First-frame image prompts would normally call LLM; isolate paid service only.
    import pixelle_video.pipelines.standard as standard

    async def prompts(*args, **kwargs):
        return ["阳光里的书桌"] * 3

    monkeypatch.setattr(standard, "generate_image_prompts", prompts)
    params = {
        "text": "第一幕。\n\n第二幕。\n\n第三幕。",
        "mode": "fixed",
        "title": "初始标题",
        "template_id": assets[0]["id"],
        "tts_inference_mode": "local",
        "media_workflow": "api/dashscope/wan2.7-image",
    }
    job = platform.jobs.submit("video.generate", params, work_id="runtime1")
    await platform.generate_video(job)
    assert not list(temp_root.iterdir())
    for action, values in [
        ("update_title", {"title": "更新标题"}),
        (
            "regenerate_frame_audio",
            {"frame_index": 1, "narration": "修改旁白", "tts_overrides": {"tts_speed": 1.1}},
        ),
        ("regenerate_frame_media", {"frame_index": 0, "image_prompt": "新的阳光"}),
        ("regenerate_all_audio", {"tts_overrides": {"tts_speed": 1}}),
        ("replace_template", {"template_id": assets[1]["id"]}),
        (
            "insert_frame",
            {
                "position": 1,
                "narration": "插入共享画面",
                "image_prompt": None,
                "image_source_index": 0,
            },
        ),
        ("delete_frame", {"frame_index": 1}),
        ("update_bgm", {"bgm_id": None, "bgm_volume": 0.1}),
        ("recompose", {}),
    ]:
        edit_job = {
            "id": job["id"],
            "work_id": "runtime1",
            "params": {"action": action, "values": values},
        }
        await platform.edit_video(edit_job)
        assert not list(temp_root.iterdir())
        assert (await platform.persistence._manifest("runtime1"))["metadata"][
            "status"
        ] == "completed"
    manifest = await platform.persistence._manifest("runtime1")
    assert manifest["storyboard"]["title"] == "更新标题"
    assert manifest["storyboard"]["frames"][1]["narration"] == "修改旁白"
    assert len(manifest["storyboard"]["frames"]) == 3
    assert manifest["metadata"]["input"]["template_id"] == assets[1]["id"]
    previous = platform.persistence._row("runtime1")["manifest"]
    broken = True
    with pytest.raises(ValueError):
        await platform.edit_video(
            {
                "id": job["id"],
                "work_id": "runtime1",
                "params": {
                    "action": "regenerate_frame_media",
                    "values": {"frame_index": 0, "image_prompt": "失败请求"},
                },
            }
        )
    assert platform.persistence._row("runtime1")["manifest"] == previous
    assert not list(temp_root.iterdir())


async def test_concurrent_template_browsers_are_isolated(tmp_path):
    from pixelle_video.services.frame_html import HTMLFrameGenerator

    template = tmp_path / "320x480/static_test.html"
    template.parent.mkdir()
    template.write_text("<h1>{{title}}</h1>")

    async def render(name):
        generator = HTMLFrameGenerator(str(template))
        try:
            await generator.generate_frame(
                name, "", image="", output_path=str(tmp_path / (name + ".png"))
            )
            return id(generator._runtime.get()["browser"])
        finally:
            await generator.close_browser()

    ids = await asyncio.gather(render("one"), render("two"))
    assert ids[0] != ids[1]
    assert (tmp_path / "one.png").is_file() and (tmp_path / "two.png").is_file()


async def test_bundled_covers_are_static_and_rebuild_only_on_template_changes(platform, tmp_path):
    from pixelle_video.platform.template_previews import prepare_template_covers

    folder = tmp_path / "templates/320x480"
    folder.mkdir(parents=True)
    source = folder / "static_test.html"
    source.write_text("<html><h1>{{title}}</h1><p>{{text}}</p></html>")
    assert (await prepare_template_covers(tmp_path))["generated"] == 1
    assert (await prepare_template_covers(tmp_path))["skipped"] == 1
    await import_resources(platform, tmp_path)
    asset = platform.resources.list("template")[0]
    first_url = asset["metadata"]["preview_url"]
    assert first_url.startswith("/template-previews/")
    assert not platform.storage.uploads
    with TestClient(create_app(platform)) as client:
        client.post("/api/auth/login", json={"username": "creator", "password": "test-password"})
        response = client.get("/api/resources").json()
        assert response[0]["preview_url"] == first_url
        assert "object_key" not in response[0]
    source.write_text("<html><h1>新版 {{title}}</h1></html>")
    assert (await prepare_template_covers(tmp_path))["generated"] == 1
    await import_resources(platform, tmp_path)
    assert platform.resources.list("template")[0]["metadata"]["preview_url"] != first_url
    assert not platform.storage.uploads


def test_frontend_root_and_deep_links(platform, tmp_path, monkeypatch):
    import importlib

    module = importlib.import_module("api.app")
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>studio</html>")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    with TestClient(create_app(platform)) as client:
        for path in ("/", "/works", "/works/work1"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.text == "<html>studio</html>"
        assert client.get("/api/missing").status_code == 404
        assert client.get("/%2e%2e/%2e%2e/config.yaml").status_code == 404


async def test_edge_refusal_has_actionable_message(platform, monkeypatch, tmp_path):
    from aiohttp import WSServerHandshakeError

    from pixelle_video.services import tts_service

    async def refused(**kwargs):
        raise WSServerHandshakeError(None, (), status=403)

    monkeypatch.setattr(tts_service, "edge_tts", refused)
    with pytest.raises(ValueError, match="请选择 MiniMax"):
        await tts_service.TTSService({})(
            "测试", inference_mode="local", output_path=str(tmp_path / "voice.mp3")
        )


async def test_llm_balance_failure_has_actionable_message(platform, monkeypatch):
    from pixelle_video.services.llm_service import LLMService

    class BalanceError(Exception):
        status_code = 402

    async def create(**kwargs):
        raise BalanceError("Insufficient Balance")

    client = SimpleNamespace(
        base_url="https://provider.test", chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    service = LLMService({})
    monkeypatch.setattr(service, "_create_client", lambda **kwargs: client)
    with pytest.raises(ValueError, match="账户余额不足"):
        await service("写三段旁白")


def test_cos_signed_token_and_object_key_scope(monkeypatch):
    from urllib.parse import parse_qs, urlparse

    from pixelle_video.platform.storage import COSStorage

    for key, value in {
        "COS_BUCKET": "test-123456",
        "COS_REGION": "ap-guangzhou",
        "COS_SECRET_ID": "test-id",
        "COS_SECRET_KEY": "test-key",
        "COS_SESSION_TOKEN": "test-token",
        "COS_PREFIX": "studio",
    }.items():
        monkeypatch.setenv(key, value)
    storage = COSStorage()
    url = storage.signed_url("works/a/final.mp4")
    assert parse_qs(urlparse(url).query)["x-cos-security-token"] == ["test-token"]
    assert "studio/works/a/final.mp4" in urlparse(url).path
    upload_url = storage.signed_url("resources/image/test.png", "PUT")
    assert "x-cos-acl" in parse_qs(urlparse(upload_url).query)["q-header-list"][0]
    for value in ["/etc/passwd", "../other", "works/../../other", "a\\b"]:
        with pytest.raises(ValueError):
            storage.key(value)


async def test_failed_delete_keeps_directory_for_retry(platform):
    await seed(platform)
    original = platform.storage.delete_prefix

    async def fail(prefix):
        raise RuntimeError("COS delete unavailable")

    platform.storage.delete_prefix = fail
    with pytest.raises(RuntimeError):
        await platform.persistence.delete_task("work1")
    assert platform.persistence._row("work1")
    platform.storage.delete_prefix = original
    assert await platform.persistence.delete_task("work1")
    assert platform.persistence._row("work1") is None
    assert not any(k.startswith("works/work1/") for k in platform.storage.objects)


async def test_running_cancel_and_secret_error(platform):
    from pixelle_video.platform.security import safe_error

    config_manager.update({"llm": {"api_key": "test-sensitive-key"}})
    assert "test-sensitive-key" not in safe_error(ValueError("Rejected test-sensitive-key"))
    manager = JobManager(platform.database)

    async def handler(job):
        while True:
            manager.check_cancel(job["id"])
            await asyncio.sleep(0.02)

    manager.register("article.generate", handler)
    job = manager.submit("article.generate", {})
    await manager.start()
    for _ in range(100):
        if manager.get(job["id"])["status"] == "running":
            break
        await asyncio.sleep(0.01)
    manager.cancel(job["id"])
    for _ in range(100):
        if manager.get(job["id"])["status"] == "cancelled":
            break
        await asyncio.sleep(0.02)
    await manager.stop()
    assert manager.get(job["id"])["status"] == "cancelled"
