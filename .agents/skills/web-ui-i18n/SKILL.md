---
name: web-ui-i18n
description: >
  改动 Streamlit Web UI 或新增界面文案时使用。适用于"加个输入框"、"改 web/ 下的页面或组件"、
  "新增 UI 文案"、"改翻译"等任务。改动 `web/` 前必读。
---

# Web UI 与国际化

## 结构

```
web/
├── app.py                  # Streamlit 入口
├── pages/                  # 多页应用：1_🎬_Home.py、2_📚_History.py
├── components/             # 可复用组件（settings、style_config、content_input...）
├── pipelines/              # Streamlit 侧的流程调用层（不是核心 pipeline！）
├── state/                  # 会话状态
├── utils/
└── i18n/locales/{zh_CN,en_US}.json
```

**注意**：`web/pipelines/` 是 UI 调用层，核心流程在 `pixelle_video/pipelines/`。
两者名字相同但职责不同，别改错地方。

## i18n 规则（硬性）

调用：

```python
from web.i18n import tr
st.text_input(tr("content_style.label"))
tr("error.missing_field", field="API Key")   # 支持 **kwargs 插值
```

新增文案：

1. 在 `web/i18n/locales/zh_CN.json` 的 `"t"` 下加 key
2. **同时在 `en_US.json` 加完全相同的 key**
3. 组件里用 `tr("your.new.key")`

**当前两个 locale 各有 576 个 key 且完全对齐**，守卫测试 `tests/test_i18n.py` 会保护这一点。
禁止只加一边，禁止在组件里硬编码中英文字符串。

## key 命名

点分结构，`{模块}.{元素}`：`app.title`、`section.tts`、`content_style.word_range_label`。
新增前先搜一下有没有语义相近的 key 可复用。

## 硬规则

1. **文案一律走 `tr()`**，组件里不出现裸字符串字面量。
2. **`fallback` 参数只在 key 确实可能缺失时用**，不要靠它掩盖漏加的 key。
3. **改 `config.yaml` 相关的设置项**时，`web/components/settings.py` 和
   `config/schema.py`、`config.example.yaml` 三处要同步。
4. Streamlit 页面不在 ruff 检查范围，但**仍要遵守 i18n 与版权头规则**。

## 参考

- [references/guide.md](references/guide.md) — 组件清单、状态管理、常见坑
