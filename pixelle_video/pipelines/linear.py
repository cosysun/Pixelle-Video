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
        # 1. Initialize context
        ctx = PipelineContext(
            input_text=text,
            params=kwargs,
            progress_callback=progress_callback
        )
        
        try:
            # === Phase 1: Preparation ===
            await self.setup_environment(ctx)
            
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
            raise

    # ==================== Lifecycle Methods ====================
    
    async def setup_environment(self, ctx: PipelineContext):
        """Step 1: Setup task directory and environment."""
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
