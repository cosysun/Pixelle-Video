# Web UI · 细节

## 组件清单

| 组件 | 职责 | 行数 |
|---|---|---|
| `components/settings.py` | 设置面板（模型、API Key、代理） | 679 |
| `components/style_config.py` | 分镜模板 / 视觉风格配置 | 1166（最长） |
| `components/content_input.py` | 主题/脚本输入、分割方式、内容风格 | ~250 |
| `components/output_preview.py` | 生成结果预览 | — |
| `components/header.py` | 页头 | — |
| `components/faq.py` | 侧边栏 FAQ | — |
| `components/digital_tts_config.py` | 数字人 TTS 配置 | — |

## 页面

- `pages/1_🎬_Home.py` — 主流程
- `pages/2_📚_History.py` — 历史记录（1225 行，最大的 UI 文件）

Streamlit 多页应用的**文件名前缀数字决定顺序**，emoji 会显示在导航里。
新增页面按 `3_🎯_Xxx.py` 命名。

## 状态管理

`web/state/` 目录下。Streamlit 每次交互会重跑整个脚本，
跨交互的状态必须存 `st.session_state`，不要用全局变量。

## i18n 实现

`web/i18n/__init__.py`：

- `load_locales()` — 从 `locales/` 目录加载所有 JSON
- `tr(key, fallback=None, **kwargs)` — 取当前语言翻译，支持插值
- 默认语言 `en_US`，按系统 locale 探测
- key 缺失时返回 `fallback`，再没有就返回 key 本身

locale 文件结构：

```json
{
  "language_name": "简体中文",
  "t": { "app.title": "...", ... }
}
```

翻译内容在 `"t"` 下，别加到外层。

## 常见坑

- **emoji 文件名**：`pages/2_📚_History.py` 在 shell 和脚本里处理要小心编码
- **`tr()` 的 key 打错不会报错**，会静默返回 key 本身——加完 key 后实际跑一下 UI 确认显示正常
- **设置项三处同步**：`config/schema.py`（Pydantic 字段）、`config.example.yaml`（示例）、
  `web/components/settings.py`（UI 控件）
- `web/` 目录被排除在 ruff 和架构守卫之外（历史原因，见 `pyproject.toml` 与守卫白名单），
  所以这里的风格问题 CI 抓不到，**靠自觉**
- 数字人/动作迁移/图生视频的 UI 在 `web/pipelines/` 下，它们调 `pixelle_video/pipelines/` 的能力，
  改功能时先确认是哪一层的问题

## 启动

```bash
./start_web.sh          # Linux/macOS
start_web.bat           # Windows
```
