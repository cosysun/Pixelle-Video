# 媒体供应商接入 · 细节与踩坑

## 现有供应商清单

| 能力 | 聚合入口 | 已接入 provider |
|---|---|---|
| image | `image_client.py` | dashscope, seedream(ark), gemini, gpt, hunyuan, openrouter |
| video | `video_client.py` | dashscope, kling, seedance |
| tts | `tts_client.py` | minimax（另有 local Edge-TTS 与 ComfyUI 两条非 API 路径） |
| vlm | `vlm_client.py` | dashscope, gemini, gpt |
| llm | `llm_client.py` | gpt, gemini, deepseek, dashscope(qwen) |

## Client 的标准骨架

以 `image_openrouter.py` 为模板（它是目前结构最规范的）：

```python
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

class OpenRouterImageClient:
    def __init__(self, api_key=None, base_url=None, local_proxy=None, timeout=300.0):
        self.api_key = (api_key or os.getenv("OPENROUTER_API_KEY") or "").strip()
        self.base_url = (base_url or os.getenv("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.local_proxy = local_proxy
        self.timeout = timeout

    def _client(self) -> httpx.Client:          # 代理在这里注入
        kwargs = {"timeout": self.timeout}
        if self.local_proxy:
            kwargs["proxy"] = self.local_proxy
        return httpx.Client(**kwargs)

    def generate_image(self, prompt, model, save_dir, video_ratio="16:9",
                       resolution="2K", image_paths=None) -> List[str]:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        ...
        return paths                             # 返回本地路径列表
```

关键点：
- `_client()` 是可替换的工厂方法——测试正是 monkeypatch 它注入 `MockTransport`
- `base_url` 要 `.rstrip("/")`，避免拼出 `//`
- 落盘文件名带时间戳+uuid，避免并发覆盖：`f"{provider}_{int(time.time())}_{uuid4().hex[:6]}{ext}"`

## 测试的写法（照抄现有范例）

```python
def test_xxx(monkeypatch, tmp_path):
    captured = {}
    def handle(request):
        captured["url"] = str(request.url)
        captured["body"] = request.read()
        return httpx.Response(200, json={"data": [{"b64_json": ...}]})

    client = XxxClient(api_key="test-key")
    monkeypatch.setattr(client, "_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handle)))
    paths = client.generate_image(..., save_dir=str(tmp_path))
    assert captured["url"] == "https://..."
    assert b'"aspect_ratio":"9:16"' in captured["body"]
```

注意现有测试用 `importlib.util.spec_from_file_location` 直接加载模块文件——
因为 `api_services/` 包名导入在测试上下文有坑。**照抄这个写法**，不要改成正常 import。

## TTS 的特殊性

TTS 有三条互不相同的路径，别混淆：

1. `local` — Edge TTS，本地合成，无外部 API
2. `comfyui` / RunningHub — 走工作流
3. `api` — `tts_client.py` → minimax

`services/tts_service.py` 的 `inference_mode` 决定走哪条（见 `StoryboardConfig.tts_inference_mode`）。
新增 TTS provider 只影响第 3 条。

## 代理处理

`config.yaml` 的 `api_providers.common.local_proxy` 是全局代理，
每个 provider 还有 `use_proxy` 开关。两者都要尊重：
`local_proxy` 来自 common，`use_proxy` 决定该 provider 是否启用。

## 已知不一致（改到时别当成 bug，也别盲目模仿）

- `llm_client.py` 里有个 `full_to_half()` 全角转半角，是历史遗留，新代码不要依赖
- `api_services/config.py` 与 `pixelle_video/config/schema.py` **并存两套配置读取**，
  新代码优先用 `config_manager`（schema.py），老的 `Config` 类是历史包袱
- `image_processor.py` 名字像路由，实际是图像处理工具（上传/拼接/去黑边），不是 provider 路由
