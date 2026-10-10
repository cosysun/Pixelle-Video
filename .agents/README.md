# .agents — AI 协作知识库

本目录给 AI 编码智能体（Cursor / Claude Code / Codex 等）提供**按需加载**的领域知识。

## 为什么这样组织

`AGENTS.md`（仓库根）只有一页，是**索引**而非百科全书。
真正的细节放在各 skill 里，AI 只在做相关任务时才读取——避免一次性把几千行塞进上下文。

```
.agents/
└── skills/
    ├── adding-media-provider/
    │   ├── SKILL.md              # 摘要：何时用 + 怎么做
    │   └── references/guide.md   # 细节：清单、骨架、踩坑
    ├── pipeline-development/
    └── web-ui-i18n/
```

**SKILL.md 写摘要，references/guide.md 写细节。**
不要把 guide 内容抄回 SKILL.md——两层结构是为了让 AI 先读短的，需要时再读长的。

## 维护原则

1. **规则要可执行**。能写成测试的就写成测试（见 `tests/test_architecture.py`、
   `tests/test_i18n.py`），文档只写测试覆盖不到的判断性知识。
2. **代码变了就更新对应 skill**。发现 AI 按过时规则写错代码，说明文档该改了。
3. **不要堆砌通用编程常识**。只写这个项目特有的、不看代码就不知道的东西。
4. 新增 skill 时同步更新根 `AGENTS.md` 的索引表。

## 与 CI 的关系

| 约束 | 检查方式 |
|---|---|
| 外部 API 收敛在 `api_services/` | `tests/test_architecture.py`（自动） |
| 依赖方向单向 | `tests/test_architecture.py`（自动） |
| i18n 双语言 key 对齐 | `tests/test_i18n.py`（自动） |
| 代码风格 | `ruff`（仅改动文件，见 ci.yml） |
| 其余约定 | 靠本目录的文档 + 人工评审 |
