---
name: pipeline-development
description: >
  新增或修改视频生成流程时使用。适用于"加一个新 pipeline"、"改生成流程"、
  "调整分镜/配音/合成步骤"、"改 progress 上报"等任务。改动 `pixelle_video/pipelines/` 前必读。
---

# 生成流程开发

## 两个基类，先选对

| 基类 | 位置 | 何时用 |
|---|---|---|
| `BasePipeline` | `pipelines/base.py` | 流程与标准线性流程**差异很大**，需要完全自定义 `__call__` |
| `LinearVideoPipeline` | `pipelines/linear.py` | 仍是「文案 → 标题 → 配图提示 → 分镜 → 产素材 → 合成」，只改其中某几步 |

**优先继承 `LinearVideoPipeline`**——它已实现任务持久化、断点续跑、异常兜底。
只有当生命周期完全不同（如 `asset_based.py` 先分析素材再生成脚本）才用 `BasePipeline`。

## LinearVideoPipeline 的生命周期

```
setup_environment → load_resume_state → generate_content → determine_title
  → plan_visuals → initialize_storyboard → expand_subtitle_chunks
  → produce_assets → post_production → finalize → (异常) handle_exception
```

**只 override 你要改的那一步**，不要整体重写 `__call__`。参考 `standard.py` 的 override 列表。

## 硬规则

1. **通过 `self.core` 访问服务**（`self.core.llm` / `.tts` / `.media` / `.video`）。
   禁止自己 `import` 并实例化 service——`BasePipeline.__init__` 已注入。
2. **每段进度都要上报**：`self._report_progress(ctx.progress_callback, "阶段名", 百分比)`。
   百分比是 0~1 的小数。嵌套任务（如逐帧生成）要把子进度**折算后**再上报，
   参考 `standard.py:439-450` 的 `frame_progress_callback`。
3. **状态放 `PipelineContext`**（`ctx`），不要用实例属性存跨步骤数据——
   pipeline 实例可能被复用，实例属性会串味。
4. **新增参数走 `ctx.params`**，并在 `config/schema.py` 里加对应配置，不要在 `__call__` 里加硬编码默认值。
5. **不要动 `finalize` / `handle_exception`**，除非你明确知道在改什么——它们负责持久化与失败落盘。

## 断点续跑

`ctx.resume_task_id` 存在时复用已有输出目录。子类在 `load_resume_state` 里
设 `ctx.skip_content_generation = True` 可跳过文案/配图规划，直接进 `produce_assets`。
**改 `load_resume_state` 时必须保证这个开关仍生效**。

## 测试

Pipeline 依赖付费 API，测试必须 mock 到 `self.core.*` 这一层。
断言 `VideoGenerationResult` 的字段与产物路径，不断言内部步骤被调用。

## 参考

- [references/guide.md](references/guide.md) — 现有 4 个 pipeline 对比、进度上报细节、调试方法
