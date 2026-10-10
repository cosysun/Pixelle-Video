---
name: adding-media-provider
description: >
  新增或改动图像/视频/TTS/VLM 供应商接入时使用。适用于"加一个新模型"、"接 XX 的 API"、
  "新增图像/视频/TTS provider"、"改 api_services 下的客户端"等任务。
---

# 新增媒体供应商（image / video / TTS / VLM）

本项目的外部模型调用**全部收敛在 `pixelle_video/services/api_services/`**。
新增供应商 = 新增一个 client 文件 + 在对应聚合 client 里接上路由。

## 目录约定

```
pixelle_video/services/api_services/
├── image_client.py        # 聚合：按 model 名路由到具体 provider
├── image_openrouter.py    # 单个 provider 实现
├── image_seedream.py
├── image_gemini.py / image_gpt.py / image_hunyuan.py / image_dashscope.py
├── video_client.py / tts_client.py / vlm_client.py / llm_client.py
└── config.py              # Config 类，读环境变量与默认值
```

**文件命名**：`{能力}_{供应商}.py`，类名 `{供应商}{能力}Client`（如 `OpenRouterImageClient`）。

## 五步流程

1. **读同族现有文件**。加图像供应商先读 `image_openrouter.py`（结构最清晰、有测试），
   加视频读 `video_seedance.py`，加 TTS 读 `tts_minimax.py`。**照抄它们的结构**，不要另发明。
2. **写 provider client**（`api_services/{capability}_{provider}.py`）：
   - 构造签名统一：`(api_key=None, base_url=None, local_proxy=None, timeout=300.0)`
   - api_key 回退链：参数 → 环境变量 → 抛错，参考 `image_openrouter.py:22-27`
   - HTTP 走 `self._client()` 工厂方法（`httpx.Client`），代理由 `local_proxy` 注入
   - **失败要抛 `RuntimeError` 并带 provider 名**，便于上层定位
   - 产物落盘返回**本地路径列表**（`List[str]`），不返回 base64 到上层
3. **接聚合路由**：在 `image_client.py` / `video_client.py` / `tts_client.py` 的
   `__init__` 里实例化，在生成方法里按 model 名前缀分发（现有代码用 `model.lower().startswith(...)`）。
4. **注册到能力表**：图像/视频供应商还需在 `services/api_media.py` 的
   `IMAGE_MODELS` / `VIDEO_MODELS` 里登记，否则 WebUI 下拉框看不到。
5. **加配置**：`config/schema.py` 加 Pydantic 字段 → `config.example.yaml` 加示例。
   不要只改 yaml。

## 硬约束

- **禁止**在 `api_services/` 之外直接 `httpx.get/post` 或 `requests.get/post`。
  守卫测试 `tests/test_architecture.py` 会拦。
- **禁止**在测试里真实调用付费 API。用 `httpx.MockTransport` 拦截，
  参考 `tests/services/test_image_openrouter.py`（这是目前唯一一个测试，也是范例）。
- 新增能力必须补一个 mock 测试，至少覆盖：请求 URL、关键请求体字段、返回路径。

## 供应商文档缺失时

`api_media.py` 里已有先例（注释写着 "Official accessible API/model contract was not found.
Keep this model unverified until provider docs are supplied."）。
**拿不到官方文档就标注 unverified，不要猜测接口字段**。

## 参考

- 完整清单与踩坑记录见 [references/guide.md](references/guide.md)
