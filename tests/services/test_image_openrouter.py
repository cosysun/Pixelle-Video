import base64
import importlib.util
from pathlib import Path

import httpx

MODULE_PATH = (
    Path(__file__).parents[2]
    / "pixelle_video"
    / "services"
    / "api_services"
    / "image_openrouter.py"
)
SPEC = importlib.util.spec_from_file_location("image_openrouter", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
OpenRouterImageClient = MODULE.OpenRouterImageClient


def test_openrouter_image_client_writes_base64_image(monkeypatch, tmp_path):
    captured = {}

    def handle(request):
        captured["url"] = str(request.url)
        captured["body"] = request.read()
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(b"png-data").decode()}]},
        )

    client = OpenRouterImageClient(api_key="test-key")
    monkeypatch.setattr(
        client,
        "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handle)),
    )

    paths = client.generate_image(
        prompt="a red panda",
        model="bytedance-seed/seedream-4.5",
        save_dir=str(tmp_path),
        video_ratio="9:16",
        resolution="1080P",
    )

    assert captured["url"] == "https://openrouter.ai/api/v1/images"
    assert b'"aspect_ratio":"9:16"' in captured["body"]
    assert b'"resolution":"1K"' in captured["body"]
    assert len(paths) == 1
    assert open(paths[0], "rb").read() == b"png-data"
