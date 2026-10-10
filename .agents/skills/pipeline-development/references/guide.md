# 生成流程 · 细节

## 现有 pipeline 对比

| 文件 | 基类 | 特点 | 行数 |
|---|---|---|---|
| `standard.py` | `LinearVideoPipeline` | 标准流程，override 了 8 个阶段方法 | ~600 |
| `linear.py` | `BasePipeline` | **既是基类也是实现**，线性流程的模板方法 | ~400 |
| `asset_based.py` | `BasePipeline` | 用户上传素材 → VLM 分析 → 生成脚本，流程特殊 | 1261（最长） |
| `custom.py` | `BasePipeline` | 模板/示例，含 `MySpecialPipeline`、`QuickPipeline` 示例类 | ~550 |

新增流程前先确认没有能 override 解决的现成 pipeline。

## 服务访问方式

`BasePipeline.__init__` 注入：

```python
self.core = pixelle_video_core
self.llm = core.llm          # LLMService
self.tts = core.tts          # TTSService
self.media = core.media      # MediaService
self.video = core.video      # VideoService
self.image = core.media      # 向后兼容别名，新代码用 self.media
```

**`self.image` 是 `self.media` 的别名**，历史遗留。新代码统一用 `self.media`。

## 进度上报的正确写法

```python
self._report_progress(ctx.progress_callback, "generating_narrations", 0.05)
```

嵌套进度要折算到父区间，否则进度条会跳：

```python
def frame_progress_callback(event: ProgressEvent):
    if ctx.progress_callback:
        adjusted = ProgressEvent(
            stage=event.stage,
            progress=0.4 + event.progress * 0.5,   # 映射到 [0.4, 0.9]
            ...
        )
        ctx.progress_callback(adjusted)
```

参考 `standard.py:439-450` 和 `494-505`。

## PipelineContext 字段速查

| 分组 | 字段 |
|---|---|
| 输入 | `input_text`, `params`, `progress_callback` |
| 任务 | `task_id`, `task_dir` |
| 续跑 | `resume_task_id`, `skip_content_generation` |
| 内容 | `title`, `narrations: List[str]` |
| 视觉 | `image_prompts: List[Optional[str]]` |
| 配置 | `config: StoryboardConfig`, `storyboard: Storyboard` |
| 输出 | `final_video_path`, `result: VideoGenerationResult` |

## 数据模型

- `StoryboardConfig`（`models/storyboard.py`）— 分镜参数：尺寸、分镜数、旁白字数、
  图片提示词字数、fps、TTS 模式。**必填字段 `media_width` / `media_height` 在最前**。
- `Storyboard` — 单个分镜的运行时状态
- `VideoGenerationResult` — pipeline 返回值
- `ProgressEvent`（`models/progress.py`）— 进度事件

## 调试

- 日志用 `loguru`：`from loguru import logger`
- 产物落在 `ctx.task_dir`，按 `task_id` 隔离，可直接去看中间文件
- 历史记录由 `services/history_manager.py` 管理，`web/pages/2_📚_History.py` 展示
- 断点续跑：给 pipeline 传 `resume_task_id=<已有 task_id>`

## 注意

- `web/pipelines/` 下还有一层 pipeline 封装（digital_human、action_transfer、i2v 等），
  它们是 Streamlit 侧的调用层，**不要和 `pixelle_video/pipelines/` 混淆**
- 改 `asset_based.py` 要特别小心：1261 行、无测试、逻辑最复杂
- `linear.py` 的 `finalize` / `handle_exception` 负责持久化，改动会影响所有子类的失败恢复
