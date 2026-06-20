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
History Manager Service

Business logic for history management (UI-agnostic).
Provides high-level operations on top of PersistenceService.
"""

from typing import Any, Callable, Dict, Optional

from loguru import logger

from pixelle_video.models.progress import ProgressEvent
from pixelle_video.services.persistence import PersistenceService


class HistoryManager:
    """
    History management service
    
    Provides business logic for:
    - Task listing and filtering
    - Task detail retrieval
    - Task duplication (for re-generation)
    - Task deletion
    - Future: Frame regeneration, export, etc.
    """
    
    def __init__(self, persistence: PersistenceService, task_editor=None):
        """
        Initialize history manager
        
        Args:
            persistence: PersistenceService instance
            task_editor: Optional task editing service
        """
        self.persistence = persistence
        self.task_editor = task_editor
    
    async def get_task_list(
        self,
        page: int = 1,
        page_size: int = 20,
        status: Optional[str] = None,
        sort_by: str = "created_at",
        sort_order: str = "desc"
    ) -> Dict[str, Any]:
        """
        Get paginated task list
        
        Args:
            page: Page number (1-indexed)
            page_size: Items per page
            status: Filter by status (optional)
            sort_by: Sort field (created_at, completed_at, title, duration)
            sort_order: Sort order (asc, desc)
        
        Returns:
            {
                "tasks": [...],
                "total": 100,
                "page": 1,
                "page_size": 20,
                "total_pages": 5
            }
        """
        return await self.persistence.list_tasks_paginated(
            page=page,
            page_size=page_size,
            status=status,
            sort_by=sort_by,
            sort_order=sort_order
        )
    
    async def get_task_detail(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Get full task detail including storyboard
        
        Args:
            task_id: Task ID
        
        Returns:
            {
                "metadata": {...},      # Task metadata
                "storyboard": {...}     # Storyboard data (if available)
            }
            or None if task not found
        """
        metadata = await self.persistence.load_task_metadata(task_id)
        if not metadata:
            return None
        
        storyboard = await self.persistence.load_storyboard(task_id)
        
        return {
            "metadata": metadata,
            "storyboard": storyboard,
        }
    
    async def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about all tasks
        
        Returns:
            {
                "total_tasks": 100,
                "completed": 95,
                "failed": 5,
                "total_duration": 3600.5,  # seconds
                "total_size": 1024000000,  # bytes
            }
        """
        return await self.persistence.get_statistics()
    
    async def delete_task(self, task_id: str) -> bool:
        """
        Delete a task and all its files
        
        Args:
            task_id: Task ID to delete
        
        Returns:
            True if successful, False otherwise
        """
        return await self.persistence.delete_task(task_id)
    
    async def duplicate_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Duplicate a task (get input parameters for new generation)
        
        This allows users to:
        1. Copy all generation parameters from a previous task
        2. Pre-fill the generation form
        3. Regenerate with same/modified parameters
        
        Args:
            task_id: Task ID to duplicate
        
        Returns:
            Input parameters dict or None if task not found
            {
                "text": "...",
                "mode": "generate",
                "title": "...",
                "n_scenes": 5,
                "tts_inference_mode": "local",
                "tts_voice": "...",
                ...
            }
        """
        metadata = await self.persistence.load_task_metadata(task_id)
        if not metadata:
            logger.warning(f"Task {task_id} not found for duplication")
            return None
        
        # Extract input parameters and refresh task-level options from storyboard.
        # Older metadata may not contain fields added after the task was generated.
        input_params = dict(metadata.get("input", {}))
        storyboard = await self.persistence.load_storyboard(task_id)
        config = getattr(storyboard, "config", None)
        if config is not None:
            for key in (
                "tts_inference_mode",
                "voice_id",
                "tts_workflow",
                "tts_speed",
                "tts_volume",
                "tts_provider",
                "tts_model",
                "tts_voice_id",
                "ref_audio",
                "media_workflow",
                "frame_template",
            ):
                value = getattr(config, key, None)
                if value is not None:
                    input_params[key] = value
        logger.info(f"Duplicated task {task_id} parameters")
        
        return input_params
    
    async def rebuild_index(self):
        """Rebuild task index (useful for maintenance or after manual changes)"""
        await self.persistence.rebuild_index()
    
    # ========================================================================
    # Editing Operations
    # ========================================================================

    def _require_task_editor(self):
        if self.task_editor is None:
            raise RuntimeError("Task editing service is not available")
        return self.task_editor

    async def remove_bgm(self, task_id: str) -> Dict[str, Any]:
        """Remove background music by rebuilding the final video."""
        return await self._require_task_editor().remove_bgm(task_id)

    async def update_bgm(
        self,
        task_id: str,
        bgm_path: Optional[str],
        bgm_volume: float = 0.2,
        bgm_mode: str = "loop",
    ) -> Dict[str, Any]:
        """Rebuild the final video with a new BGM setting."""
        return await self._require_task_editor().update_bgm(
            task_id,
            bgm_path,
            bgm_volume=bgm_volume,
            bgm_mode=bgm_mode,
        )

    async def regenerate_all_audio(
        self,
        task_id: str,
        tts_overrides: Dict[str, Any],
        progress_callback: Optional[Callable[[ProgressEvent], None]] = None,
    ) -> Dict[str, Any]:
        """Regenerate voiceover for every frame and rebuild the final video."""
        return await self._require_task_editor().regenerate_all_audio(
            task_id,
            tts_overrides,
            progress_callback=progress_callback,
        )

    async def regenerate_frame_audio(
        self,
        task_id: str,
        frame_index: int,
        narration: str,
        tts_overrides: Optional[Dict[str, Any]] = None,
        persist_overrides: bool = False,
    ) -> Dict[str, Any]:
        """Regenerate one frame's voiceover and rebuild the final video."""
        return await self._require_task_editor().regenerate_frame_audio(
            task_id,
            frame_index,
            narration,
            tts_overrides=tts_overrides,
            persist_overrides=persist_overrides,
        )

    async def regenerate_frame_media(
        self,
        task_id: str,
        frame_index: int,
        image_prompt: str,
        media_overrides: Optional[Dict[str, Any]] = None,
        persist_overrides: bool = False,
    ) -> Dict[str, Any]:
        """Regenerate one frame's visual media and rebuild the final video."""
        return await self._require_task_editor().regenerate_frame_media(
            task_id,
            frame_index,
            image_prompt,
            media_overrides=media_overrides,
            persist_overrides=persist_overrides,
        )

    async def insert_frame(
        self,
        task_id: str,
        position: int,
        narration: str,
        image_prompt: Optional[str],
        tts_overrides: Optional[Dict[str, Any]] = None,
        media_overrides: Optional[Dict[str, Any]] = None,
        persist_overrides: bool = False,
        image_source_index: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Insert a new storyboard frame and rebuild the final video."""
        return await self._require_task_editor().insert_frame(
            task_id,
            position,
            narration,
            image_prompt,
            tts_overrides=tts_overrides,
            media_overrides=media_overrides,
            persist_overrides=persist_overrides,
            image_source_index=image_source_index,
        )

    async def delete_frame(self, task_id: str, frame_index: int) -> Dict[str, Any]:
        """Delete a storyboard frame and rebuild the final video."""
        return await self._require_task_editor().delete_frame(task_id, frame_index)

    async def replace_template(self, task_id: str, frame_template: str) -> Dict[str, Any]:
        """Replace the task-level frame template and rebuild the final video."""
        return await self._require_task_editor().replace_template(task_id, frame_template)

    async def request_cancel_edit(self, task_id: str) -> None:
        """Request cooperative cancellation for an in-progress edit operation."""
        self._require_task_editor().request_cancel(task_id)

    # ========================================================================
    # Future Extensions
    # ========================================================================
    
    async def export_task(self, task_id: str, export_path: str) -> Optional[str]:
        """
        Export task as a package (metadata + video + frames) (FUTURE FEATURE)
        
        Args:
            task_id: Task ID to export
            export_path: Export file path (e.g., "exports/task.zip")
        
        Returns:
            Export file path or None if failed
        
        TODO: Implement in Phase 3
        - Collect all task files
        - Create ZIP archive
        - Include metadata.json, storyboard.json, video, frames
        """
        logger.warning("export_task is not implemented yet (Phase 3 feature)")
        return None

