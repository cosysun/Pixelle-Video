# Copyright (C) 2025 AIDC-AI
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Linear Video Pipeline Base Class

This module defines the template method pattern for linear video generation workflows.
It introduces `PipelineContext` for state management and `LinearVideoPipeline` for
process orchestration.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable
from loguru import logger

from pixelle_video.pipelines.base import BasePipeline
from pixelle_video.models.storyboard import (
    Storyboard,
    StoryboardFrame,
    VideoGenerationResult,
    StoryboardConfig
)
from pixelle_video.models.progress import ProgressEvent


@dataclass
class PipelineContext:
    """
    Context object holding the state of a single pipeline execution.

    This object is passed between steps in the LinearVideoPipeline lifecycle.
    """
    # === Input ===
    input_text: str
    params: Dict[str, Any]
    progress_callback: Optional[Callable[[ProgressEvent], None]] = None

    # === Task State ===
    task_id: Optional[str] = None
    task_dir: Optional[str] = None

    # === Resume support ===
    # When set (via the ``resume_task_id`` kwarg on the pipeline call), the
    # pipeline reuses the existing output directory + storyboard instead of
    # creating fresh state. Subclasses set ``skip_content_generation = True``
    # in their ``load_resume_state`` override to bypass the script/visual
    # planning lifecycle hooks and jump straight to ``produce_assets``.
    resume_task_id: Optional[str] = None
    skip_content_generation: bool = False

    # === Content ===
    title: Optional[str] = None
    narrations: List[str] = field(default_factory=list)

    # === Visuals ===
    image_prompts: List[Optional[str]] = field(default_factory=list)

    # === Configuration & Storyboard ===
    config: Optional[StoryboardConfig] = None
    storyboard: Optional[Storyboard] = None

    # === Output ===
    final_video_path: Optional[str] = None
    result: Optional[VideoGenerationResult] = None


class LinearVideoPipeline(BasePipeline):
    """
    Base class for linear video generation pipelines using the Template Method pattern.
    
    This class orchestrates the video generation process into distinct lifecycle steps:
    1. setup_environment
    2. generate_content
    3. determine_title
    4. plan_visuals
    5. initialize_storyboard
    6. produce_assets
    7. post_production
    8. finalize
    
    Subclasses should override specific steps to customize behavior while maintaining
    the overall workflow structure.
    """
    
    async def __call__(
        self,
        text: str,
        progress_callback: Optional[Callable[[ProgressEvent], None]] = None,
        **kwargs
    ) -> VideoGenerationResult:
        """
        Execute the pipeline using the template method.
        """
        # Pull resume_task_id out of kwargs so it doesn't end up in
        # ctx.params and accidentally leak into ``input`` metadata.
        resume_task_id = kwargs.pop("resume_task_id", None)

        # 1. Initialize context
        ctx = PipelineContext(
            input_text=text,
            params=kwargs,
            progress_callback=progress_callback,
            resume_task_id=resume_task_id,
        )

        try:
            # === Phase 1: Preparation ===
            await self.setup_environment(ctx)

            # Persist initial metadata so the task shows up in the History
            # index immediately. This is what makes failures recoverable: even
            # if the pipeline crashes during content generation, the user can
            # find the task on the History page (status=failed) and decide
            # whether to delete it or — for crashes after produce_assets gets
            # going — resume it.
            await self._persist_initial_metadata(ctx)

            # === Phase 1b: Resume hook ===
            # Subclasses override this to load a previously-persisted
            # storyboard from disk and set ctx.skip_content_generation=True
            # so we bypass content/visual planning and resume mid-pipeline.
            if ctx.resume_task_id:
                await self.load_resume_state(ctx)

            if not ctx.skip_content_generation:
                # === Phase 2: Content Creation ===
                await self.generate_content(ctx)
                await self.determine_title(ctx)

                # === Phase 3: Visual Planning ===
                await self.plan_visuals(ctx)
                await self.initialize_storyboard(ctx)

                # === Phase 3b: Subtitle chunking ===
                # Split overlong narrations into multiple frames sharing one image
                # so the bottom-card text never overflows. Idempotent on short text.
                await self.expand_subtitle_chunks(ctx)

            # === Phase 4: Asset Production ===
            await self.produce_assets(ctx)

            # === Phase 5: Post Production ===
            await self.post_production(ctx)

            # === Phase 6: Finalization ===
            return await self.finalize(ctx)

        except Exception as e:
            await self.handle_exception(ctx, e)
            # Best-effort failure persistence so the task surfaces on the
            # History page with the error message and a Resume button.
            # Wrapped in its own try/except — a persistence failure must not
            # mask the original pipeline exception.
            try:
                await self._persist_failure(ctx, e)
            except Exception as persist_err:  # noqa: BLE001
                logger.error(
                    f"Failed to persist task failure for {ctx.task_id}: "
                    f"{persist_err} (original error: {e})"
                )
            raise

    # ==================== Lifecycle Methods ====================
    
    async def setup_environment(self, ctx: PipelineContext):
        """Step 1: Setup task directory and environment."""
        pass

    async def load_resume_state(self, ctx: PipelineContext):
        """
        Step 1b (resume only): Load a previously-persisted storyboard from
        disk and skip content/visual planning.

        Called only when ``ctx.resume_task_id`` is set. Default no-op: a
        subclass must override this to populate ``ctx.storyboard``,
        ``ctx.config``, ``ctx.title``, ``ctx.final_video_path``, and to set
        ``ctx.skip_content_generation = True``. If the override leaves
        ``skip_content_generation`` False, the pipeline will run the normal
        content-generation path (useful for "resume but regenerate the
        script" semantics, though we don't expose that today).
        """
        pass

    async def generate_content(self, ctx: PipelineContext):
        """Step 2: Generate or process script/narrations."""
        pass
        
    async def determine_title(self, ctx: PipelineContext):
        """Step 3: Determine or generate video title."""
        pass
        
    async def plan_visuals(self, ctx: PipelineContext):
        """Step 4: Generate image prompts or visual descriptions."""
        pass
        
    async def initialize_storyboard(self, ctx: PipelineContext):
        """Step 5: Create Storyboard object and frames."""
        pass

    async def expand_subtitle_chunks(self, ctx: PipelineContext, max_chars: int = 25):
        """
        Step 5b: Split overlong narrations into subtitle-style chunks.

        Walks ``ctx.storyboard.frames``. Any frame whose narration exceeds
        ``max_chars`` characters is replaced by N consecutive sibling frames:
          - The first frame keeps the original ``image_prompt`` and gets the
            first chunk as its narration.
          - The remaining frames get ``image_prompt=None`` and
            ``image_source_index`` pointing at the first frame's new index, so
            the FrameProcessor will reuse the parent's generated media instead
            of regenerating it.

        Splitting aligns to Chinese punctuation (see
        ``pixelle_video.utils.text_split_util``). Indices are renumbered
        sequentially after expansion. Frames with already-short narrations are
        left untouched, making this hook a no-op in the common case.

        Subclasses can override (e.g. to disable chunking) by replacing this
        method with ``pass`` or by passing a different ``max_chars``.
        """
        if not ctx.storyboard or not ctx.storyboard.frames:
            return

        # Local import to keep module load light and avoid cycles.
        from pixelle_video.utils.text_split_util import split_narration_into_chunks

        new_frames: List[StoryboardFrame] = []
        expanded_count = 0
        for frame in ctx.storyboard.frames:
            chunks = split_narration_into_chunks(frame.narration or "", max_chars=max_chars)
            if len(chunks) <= 1:
                # No expansion needed (or empty narration; keep as-is).
                new_frames.append(frame)
                continue

            # Parent: reuse the original frame object so any other state
            # (image_path already set by an asset-based pipeline, etc.) is
            # preserved. Just shorten its narration to the first chunk.
            parent_new_index = len(new_frames)
            frame.narration = chunks[0]
            new_frames.append(frame)

            # Children: same image, no image_prompt, point at the parent's
            # new (post-renumber) position.
            for chunk in chunks[1:]:
                child = StoryboardFrame(
                    index=-1,  # filled in below
                    narration=chunk,
                    image_prompt=None,
                    image_source_index=parent_new_index,
                )
                new_frames.append(child)

            expanded_count += 1

        # Renumber every frame to match its new list position so external
        # consumers (persistence, History page, etc.) see a clean sequence.
        for new_pos, f in enumerate(new_frames):
            f.index = new_pos

        if expanded_count:
            logger.info(
                f"expand_subtitle_chunks: expanded {expanded_count} frame(s) "
                f"into {len(new_frames)} total frames (max_chars={max_chars})"
            )

        ctx.storyboard.frames = new_frames

    async def produce_assets(self, ctx: PipelineContext):
        """Step 6: Generate audio, images, and render frames (Core processing)."""
        pass
        
    async def post_production(self, ctx: PipelineContext):
        """Step 7: Concatenate videos and add BGM."""
        pass
        
    async def finalize(self, ctx: PipelineContext) -> VideoGenerationResult:
        """Step 8: Create result object and persist metadata."""
        raise NotImplementedError("finalize must be implemented by subclass")

    async def handle_exception(self, ctx: PipelineContext, error: Exception):
        """Handle exceptions during pipeline execution."""
        logger.error(f"Pipeline execution failed: {error}")

    # ==================== Persistence Helpers ====================
    #
    # These are called by ``__call__`` to record task lifecycle to disk so
    # the History page can surface failed/resumable tasks. Each helper is
    # tolerant of a missing ``persistence`` service or missing ``task_id`` —
    # subclasses that don't use the persistence layer (custom pipelines, in-
    # memory tests) get pure no-ops.

    def _build_input_metadata(self, ctx: PipelineContext) -> Dict[str, Any]:
        """
        Build the ``input`` block stored in metadata.json.

        Filters out non-serializable / heavy values (lambdas, file handles,
        progress callbacks). Subclasses can override to add pipeline-specific
        keys, but the default is "everything in ctx.params that survives
        JSON serialization, plus the original input text and the source-page
        tag if the caller provided one in kwargs."
        """
        import json

        def _is_jsonable(v: Any) -> bool:
            try:
                json.dumps(v)
                return True
            except (TypeError, ValueError):
                return False

        safe_params = {
            k: v for k, v in (ctx.params or {}).items()
            if _is_jsonable(v) and not callable(v)
        }
        safe_params["text"] = ctx.input_text
        # source_page is set by the UI wrapper so the History page's Resume
        # button knows which page to route back to. Falls back to the Home
        # page since both Standard and AssetBased UIs live there.
        safe_params.setdefault(
            "source_page",
            ctx.params.get("source_page", "1_🎬_Home"),
        )
        # source_pipeline is the UI-side pipeline name (e.g. "quick_create",
        # "custom_media"). The History page uses it to write a *per-pipeline*
        # session-state key when the user clicks 🔄, so each tab on the Home
        # page only consumes its own resume hint instead of fighting over a
        # shared key. Left as None for tasks that pre-date this field.
        safe_params.setdefault(
            "source_pipeline",
            ctx.params.get("source_pipeline"),
        )
        return safe_params

    async def _persist_initial_metadata(self, ctx: PipelineContext):
        """
        Save metadata.json with status=running so the task is visible in the
        History index from the very start. **Best-effort**: never raises —
        a disk error here must not abort the pipeline before any user-facing
        work has even started.

        No-op if persistence is unavailable (e.g. ``setup_environment`` didn't
        allocate a task_id, or the core wasn't fully initialized).
        """
        if not ctx.task_id or not getattr(self.core, "persistence", None):
            return

        try:
            # On resume, do not clobber the existing completed_at / result block
            # in metadata.json. Update status back to "running" but keep input.
            existing = await self.core.persistence.load_task_metadata(ctx.task_id)
            if existing:
                existing["status"] = "running"
                existing.pop("error", None)
                existing.pop("completed_at", None)
                await self.core.persistence.save_task_metadata(ctx.task_id, existing)
                return

            metadata = {
                "task_id": ctx.task_id,
                "created_at": datetime.now().isoformat(),
                "status": "running",
                "input": self._build_input_metadata(ctx),
            }
            await self.core.persistence.save_task_metadata(ctx.task_id, metadata)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"Could not persist initial metadata for {ctx.task_id}: {e} "
                f"(continuing — task will still run, but won't show up on the "
                f"History page until finalize)"
            )

    async def _persist_failure(self, ctx: PipelineContext, error: Exception):
        """
        On unhandled pipeline exception: mark the task failed and flush any
        per-frame progress that hasn't been checkpointed yet.

        Storyboard is saved best-effort — even an in-progress storyboard is
        valuable for resume because per-frame ``video_segment_path`` values
        on completed frames let ``FrameProcessor`` skip them.
        """
        if not ctx.task_id or not getattr(self.core, "persistence", None):
            return

        if ctx.storyboard is not None:
            try:
                await self.core.persistence.save_storyboard(ctx.task_id, ctx.storyboard)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Could not persist storyboard on failure: {e}")

        await self.core.persistence.update_task_status(
            ctx.task_id,
            "failed",
            error=str(error),
        )
