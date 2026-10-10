# Pixelle-Video — AI 协作约定

AI 全自动短视频引擎。输入主题 → LLM 生成文案 → 配图/视频 → TTS 配音 → BGM → 合成视频。

**技术栈**：Python 3.11+ / FastAPI (`api/`) / Streamlit (`web/`) / Pydantic / loguru / uv
**代码规模**：约 2 万行 Python，136 个源文件

## 仓库边界

改代码前先确认归属，避免在这里解决属于别处的问题。

| 位置 | 归属 |
|---|---|
| `pixelle_video/` | 核心能力层：pipelines、services、models、config |
| `api/` | FastAPI 对外 HTTP 接口 |
| `web/` | Streamlit UI（页面、组件、i18n） |
| `workflows/` | ComfyUI / RunningHub 工作流 JSON |
| 图像/视频/TTS 模型服务 | 外部供应商；本项目只做接入，不改供应商行为 |

**依赖方向单向**：`config` → `services` → `pipelines` → `web`/`api`。
下层不得反向 import 上层（`pixelle_video/` 里不允许 import `web` 或 `api`）。

## 必读模块

| 改动目标 | 先读 |
|---|---|
| 新增生成流程 | `pixelle_video/pipelines/base.py` |
| 新增模型供应商 | `pixelle_video/services/api_services/` 同族文件 |
| 配置项 | `pixelle_video/config/schema.py`（唯一真源） |
| 服务入口 | `pixelle_video/service.py`（`PixelleVideoCore`） |
| 提示词 | `pixelle_video/prompts/` |

## 硬规则

以下由人或 CI 检查，违反即为缺陷：

1. **外部 API 调用必须收敛在 `pixelle_video/services/api_services/`**。
   禁止在 pipelines / web / utils 里直接 `httpx.get/post`、`requests.get/post`。
   守卫测试：`tests/test_architecture.py`。

2. **新增配置项先在 `config/schema.py` 的 Pydantic 模型加字段**，再补 `config.example.yaml`。
   禁止用 `os.getenv()` 散落读取新配置——配置统一走 `config_manager`。

3. **新增 pipeline 必须继承 `BasePipeline` 并实现 `async __call__`**，
   通过 `self.core` 访问服务，不得自行 `import` 并实例化 service。

4. **UI 文案必须走 i18n**：`web/i18n/locales/zh_CN.json` 与 `en_US.json` **同步新增 key**，
   禁止在组件里硬编码中文/英文字符串。

5. **每个新源文件带 Apache 2.0 版权头**（现有 99/136 个文件已有，照抄同目录文件开头）。

6. **提交前必须过 `ruff check` 与 `pytest`**。`ruff` 规则：E/F/I，行宽 100。

## 测试约定

测试严重不足（当前仅 1 个文件），新增代码请补测试，优先保证：

- **Mock 外部 HTTP**：用 `httpx.MockTransport`（参考 `tests/services/test_image_openrouter.py`），
  禁止在测试里真实调用付费 API。
- **测试行为而非实现**：断言返回的路径、文件内容、请求体，不断言内部函数被调用。
- 测试放 `tests/`，镜像 `pixelle_video/` 目录结构。

## 已知技术债

改动这些文件时格外小心，它们是历史遗留的大文件：

| 文件 | 行数 | 说明 |
|---|---|---|
| `pixelle_video/pipelines/asset_based.py` | 1261 | 最长的 pipeline |
| `web/pages/2_📚_History.py` | 1225 | Streamlit 页面 |
| `web/components/style_config.py` | 1166 | 样式配置组件 |
| `pixelle_video/services/video.py` | 1004 | 视频合成 |

`web/` 下的 Streamlit 文件不在 ruff/架构守卫的检查范围内（见 `pyproject.toml` 与守卫测试白名单），
但 i18n 规则同样适用。

## 提交与 PR

- 提交信息用 Conventional Commits：`feat:` / `fix:` / `docs:` / `chore:` / `refactor:`
- **不要提交 `config.yaml`**（含密钥），只提交 `config.example.yaml`
- AI 生成的代码须在 PR 描述中说明**验证方式**：跑了什么命令、真实调用还是 mock。
  涉及图像/视频/TTS 的改动，若未真实跑通，明确说明「未实测」而非声称可用。

## 领域知识（按需加载）

改动前先读对应的 skill，避免凭空猜测：

| Skill | 何时读 |
|---|---|
| [adding-media-provider](.agents/skills/adding-media-provider/SKILL.md) | 新增图像/视频/TTS/VLM 供应商 |
| [pipeline-development](.agents/skills/pipeline-development/SKILL.md) | 新增或修改生成流程 |
| [web-ui-i18n](.agents/skills/web-ui-i18n/SKILL.md) | 改 Streamlit UI、新增文案 |

详细规则在各 skill 的 `references/guide.md`，不要把它们抄回本文件。
