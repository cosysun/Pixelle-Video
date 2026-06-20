# Copyright (C) 2025 AIDC-AI
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""
Task edit service.

Provides UI-agnostic operations for editing an already generated task:
regenerate audio/media for one or more frames, then rebuild the final video
from the scene segments.
"""

from __future__ import annotations

import copy
import os
import shutil
from dataclasses import fields, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from loguru import logger

from pixelle_video.models.progress import ProgressEvent
from pixelle_video.models.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from pixelle_video.services.video import VideoService
from pixelle_video.utils.template_util import get_template_type


class TaskEditCancelled(RuntimeError):
    """Raised when a persisted task edit was cancelled cooperatively."""


class TaskEditService:
    """Edit persisted video tasks without depending on the Web UI."""

    def __init__(self, pixelle_video_core):
        self.core = pixelle_video_core
        self.persistence = pixelle_video_core.persistence

    async def remove_bgm(self, task_id: str) -> Dict[str, Any]:
        """Rebuild final video from existing scene segments without BGM."""
        storyboard, metadata = await self._load_task(task_id)
        await self.create_revision_backup(task_id)
        metadata.setdefault("input", {})["bgm_path"] = None
        metadata["input"]["bgm_volume"] = 0.0
        return await self._rebuild_final_video(
            task_id=task_id,
            storyboard=storyboard,
            metadata=metadata,
            bgm_path=None,
        )

    async def update_bgm(
        self,
        task_id: str,
        bgm_path: Optional[str],
        bgm_volume: float = 0.2,
        bgm_mode: str = "loop",
    ) -> Dict[str, Any]:
        """Rebuild final video with a new BGM setting."""
        storyboard, metadata = await self._load_task(task_id)
        await self.create_revision_backup(task_id)
        metadata.setdefault("input", {})["bgm_path"] = bgm_path
        metadata["input"]["bgm_volume"] = bgm_volume
        metadata["input"]["bgm_mode"] = bgm_mode
        return await self._rebuild_final_video(
            task_id=task_id,
            storyboard=storyboard,
            metadata=metadata,
            bgm_path=bgm_path,
            bgm_volume=bgm_volume,
            bgm_mode=bgm_mode,
        )

    async def regenerate_frame_audio(
        self,
        task_id: str,
        frame_index: int,
        narration: str,
        tts_overrides: Optional[Dict[str, Any]] = None,
        persist_overrides: bool = False,
    ) -> Dict[str, Any]:
        """Regenerate one frame's voiceover and dependent video segment."""
        storyboard, metadata = await self._load_task(task_id)
        frame = self._get_frame(storyboard, frame_index)
        await self.create_revision_backup(task_id, changed_frame_indexes=[frame_index])

        frame.narration = narration
        self._clear_audio_outputs(frame)

        processing_config = self._config_with_overrides(storyboard.config, tts_overrides)
        if persist_overrides:
            storyboard.config = processing_config

        await self._process_frame(storyboard, frame, processing_config)
        return await self._rebuild_final_video(task_id, storyboard, metadata)

    async def regenerate_all_audio(
        self,
        task_id: str,
        tts_overrides: Dict[str, Any],
        progress_callback: Optional[Callable[[ProgressEvent], None]] = None,
    ) -> Dict[str, Any]:
        """Regenerate all frame audio with updated task-level TTS config."""
        storyboard, metadata = await self._load_task(task_id)
        self.clear_cancel_request(task_id)
        await self.create_revision_backup(
            task_id,
            changed_frame_indexes=[frame.index for frame in storyboard.frames],
        )

        storyboard.config = self._config_with_overrides(storyboard.config, tts_overrides)
        total_frames = len(storyboard.frames)
        if progress_callback:
            progress_callback(ProgressEvent("editing_all_audio", 0.0, extra_info="start"))

        for i, frame in enumerate(storyboard.frames):
            await self._raise_if_cancelled(task_id, storyboard, metadata)
            self._clear_audio_outputs(frame)
            await self._process_frame(
                storyboard,
                frame,
                storyboard.config,
                progress_callback=self._wrap_frame_progress(progress_callback, i, total_frames),
            )
            if progress_callback:
                progress_callback(
                    ProgressEvent(
                        "editing_all_audio",
                        (i + 1) / total_frames if total_frames else 1.0,
                        frame_current=i + 1,
                        frame_total=total_frames,
                        extra_info="frame_completed",
                    )
                )

        if progress_callback:
            progress_callback(ProgressEvent("editing_all_audio", 1.0, extra_info="completed"))
        self.clear_cancel_request(task_id)

        return await self._rebuild_final_video(task_id, storyboard, metadata)

    async def regenerate_frame_media(
        self,
        task_id: str,
        frame_index: int,
        image_prompt: str,
        media_overrides: Optional[Dict[str, Any]] = None,
        persist_overrides: bool = False,
    ) -> Dict[str, Any]:
        """Regenerate one frame's image/video media and dependent segment."""
        storyboard, metadata = await self._load_task(task_id)
        frame = self._get_frame(storyboard, frame_index)
        related_frames = self._media_related_frames(storyboard, frame_index)
        related_indexes = [related.index for related in related_frames]
        await self.create_revision_backup(task_id, changed_frame_indexes=related_indexes)

        frame.image_prompt = image_prompt
        for related in related_frames:
            self._clear_media_outputs(related)

        processing_config = self._config_with_overrides(storyboard.config, media_overrides)
        if persist_overrides:
            storyboard.config = processing_config

        for related in related_frames:
            await self._process_frame(storyboard, related, processing_config)
        return await self._rebuild_final_video(task_id, storyboard, metadata)

    async def replace_template(self, task_id: str, frame_template: str) -> Dict[str, Any]:
        """Replace the task-level frame template and rebuild all segments."""
        storyboard, metadata = await self._load_task(task_id)
        self._ensure_compatible_template(storyboard.config.frame_template, frame_template)
        await self.create_revision_backup(
            task_id,
            changed_frame_indexes=[frame.index for frame in storyboard.frames],
        )

        storyboard.config.frame_template = frame_template
        for frame in storyboard.frames:
            self._clear_template_outputs(frame)
            await self._process_frame(storyboard, frame, storyboard.config)

        return await self._rebuild_final_video(task_id, storyboard, metadata)

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
        """Insert a new storyboard frame, process it, and rebuild the final video."""
        storyboard, metadata = await self._load_task(task_id)
        if position < 0 or position > len(storyboard.frames):
            raise IndexError(f"Insert position {position} out of range")

        narration = (narration or "").strip()
        if not narration:
            raise ValueError("Narration is required for a new frame")

        image_prompt = (image_prompt or "").strip() or None
        if image_source_index is not None:
            parent = self._get_frame(storyboard, image_source_index)
            if parent.image_source_index is not None:
                raise ValueError("Child frames can only be created from a parent frame")
        elif self._template_requires_media(storyboard.config) and not image_prompt:
            raise ValueError("Image prompt is required for the current frame template")

        await self.create_revision_backup(
            task_id,
            changed_frame_indexes=[frame.index for frame in storyboard.frames[position:]],
        )
        self._shift_standard_frame_artifacts_for_insert(task_id, storyboard.frames, position)

        new_frame = StoryboardFrame(
            index=-1,
            narration=narration,
            image_prompt=image_prompt,
            image_source_index=image_source_index,
        )
        storyboard.frames.insert(position, new_frame)
        self._renumber_frames(storyboard)
        self._sync_frame_counts(storyboard, metadata)

        processing_overrides: Dict[str, Any] = {}
        processing_overrides.update(tts_overrides or {})
        processing_overrides.update(media_overrides or {})
        processing_config = self._config_with_overrides(storyboard.config, processing_overrides)
        if persist_overrides:
            storyboard.config = processing_config

        await self._process_frame(storyboard, new_frame, processing_config)
        return await self._rebuild_final_video(task_id, storyboard, metadata)

    async def delete_frame(self, task_id: str, frame_index: int) -> Dict[str, Any]:
        """Delete a storyboard frame and dependent subtitle-child frames."""
        storyboard, metadata = await self._load_task(task_id)
        self._get_frame(storyboard, frame_index)
        delete_indexes = self._cascade_delete_indexes(storyboard, frame_index)
        if len(storyboard.frames) - len(delete_indexes) <= 0:
            raise ValueError("Cannot delete all storyboard frames")

        await self.create_revision_backup(
            task_id,
            changed_frame_indexes=[frame.index for frame in storyboard.frames],
        )

        self._remove_standard_frame_artifacts(task_id, delete_indexes)
        storyboard.frames = [frame for frame in storyboard.frames if frame.index not in delete_indexes]
        self._realign_standard_frame_artifacts(task_id, storyboard.frames)
        self._renumber_frames(storyboard)
        self._sync_frame_counts(storyboard, metadata)
        return await self._rebuild_final_video(task_id, storyboard, metadata)

    async def create_revision_backup(
        self,
        task_id: str,
        changed_frame_indexes: Optional[Iterable[int]] = None,
    ) -> str:
        """
        Save a best-effort backup of current task metadata/storyboard/final video
        and frame artifacts that are about to be replaced.
        """
        task_dir = self.persistence.get_task_dir(task_id)
        revision_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        revision_dir = task_dir / "revisions" / revision_id
        revision_dir.mkdir(parents=True, exist_ok=True)

        for name in ("metadata.json", "storyboard.json", "final.mp4"):
            src = task_dir / name
            if src.exists():
                shutil.copy2(src, revision_dir / name)

        frames_dir = task_dir / "frames"
        if changed_frame_indexes is not None and frames_dir.exists():
            backup_frames_dir = revision_dir / "frames"
            backup_frames_dir.mkdir(parents=True, exist_ok=True)
            prefixes = {f"{index + 1:02d}_" for index in changed_frame_indexes}
            for path in frames_dir.iterdir():
                if path.is_file() and any(path.name.startswith(prefix) for prefix in prefixes):
                    shutil.copy2(path, backup_frames_dir / path.name)

        logger.info(f"Created task revision backup: {revision_dir}")
        return str(revision_dir)

    def request_cancel(self, task_id: str) -> None:
        """Request cooperative cancellation for the current edit operation."""
        marker = self._cancel_marker_path(task_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(datetime.now().isoformat(), encoding="utf-8")

    def clear_cancel_request(self, task_id: str) -> None:
        """Clear a previous cancellation request before a new edit starts."""
        try:
            self._cancel_marker_path(task_id).unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Failed to clear cancel marker for {task_id}: {e}")

    async def _load_task(self, task_id: str) -> tuple[Storyboard, Dict[str, Any]]:
        storyboard = await self.persistence.load_storyboard(task_id)
        if storyboard is None:
            raise ValueError(f"Task {task_id} has no storyboard.json")

        metadata = await self.persistence.load_task_metadata(task_id)
        if metadata is None:
            raise ValueError(f"Task {task_id} has no metadata.json")

        return storyboard, metadata

    def _cancel_marker_path(self, task_id: str):
        return self.persistence.get_task_dir(task_id) / ".edit_cancel_requested"

    async def _raise_if_cancelled(
        self,
        task_id: str,
        storyboard: Storyboard,
        metadata: Dict[str, Any],
    ) -> None:
        if not self._cancel_marker_path(task_id).exists():
            return

        metadata["status"] = "cancelled"
        metadata["completed_at"] = datetime.now().isoformat()
        await self.persistence.save_storyboard(task_id, storyboard)
        await self.persistence.save_task_metadata(task_id, metadata)
        raise TaskEditCancelled(f"Task edit cancelled: {task_id}")

    def _get_frame(self, storyboard: Storyboard, frame_index: int) -> StoryboardFrame:
        for frame in storyboard.frames:
            if frame.index == frame_index:
                return frame
        raise IndexError(f"Frame {frame_index} not found")

    def _media_related_frames(self, storyboard: Storyboard, frame_index: int) -> list[StoryboardFrame]:
        related = [
            frame
            for frame in storyboard.frames
            if frame.index == frame_index or frame.image_source_index == frame_index
        ]
        return sorted(related, key=lambda frame: frame.index)

    def _cascade_delete_indexes(self, storyboard: Storyboard, frame_index: int) -> set[int]:
        delete_indexes = {frame_index}
        changed = True
        while changed:
            changed = False
            for frame in storyboard.frames:
                if frame.image_source_index in delete_indexes and frame.index not in delete_indexes:
                    delete_indexes.add(frame.index)
                    changed = True
        return delete_indexes

    def _renumber_frames(self, storyboard: Storyboard) -> None:
        old_indexes = {id(frame): frame.index for frame in storyboard.frames}
        old_to_new = {
            old_index: new_index
            for new_index, frame in enumerate(storyboard.frames)
            if (old_index := old_indexes[id(frame)]) >= 0
        }

        for new_index, frame in enumerate(storyboard.frames):
            if frame.image_source_index is not None:
                if frame.image_source_index not in old_to_new:
                    raise ValueError(
                        f"Frame {frame.index} references deleted frame "
                        f"{frame.image_source_index}"
                    )
                frame.image_source_index = old_to_new[frame.image_source_index]
            frame.index = new_index

    def _sync_frame_counts(self, storyboard: Storyboard, metadata: Dict[str, Any]) -> None:
        frame_count = len(storyboard.frames)
        storyboard.config.n_storyboard = frame_count
        metadata.setdefault("input", {})["n_scenes"] = frame_count

    def _shift_standard_frame_artifacts_for_insert(
        self,
        task_id: str,
        frames: list[StoryboardFrame],
        position: int,
    ) -> None:
        """Move standard 01_* frame files out of the inserted frame's target slot."""
        if position >= len(frames):
            return

        moved_paths: Dict[str, str] = {}
        moved_fields: set[tuple[int, str]] = set()

        for frame in sorted(frames, key=lambda item: item.index, reverse=True):
            old_index = frame.index
            if old_index < position:
                continue

            for attr, file_type in self._artifact_fields():
                current_path = getattr(frame, attr, None)
                if not current_path:
                    continue

                old_standard_path = self._standard_frame_artifact_path(
                    task_id,
                    old_index,
                    file_type,
                )
                if os.path.abspath(current_path) != os.path.abspath(old_standard_path):
                    continue

                new_standard_path = self._standard_frame_artifact_path(
                    task_id,
                    old_index + 1,
                    file_type,
                )
                if os.path.exists(current_path):
                    os.makedirs(os.path.dirname(new_standard_path), exist_ok=True)
                    if os.path.exists(new_standard_path):
                        os.remove(new_standard_path)
                    shutil.move(current_path, new_standard_path)
                moved_paths[os.path.abspath(current_path)] = new_standard_path
                setattr(frame, attr, new_standard_path)
                moved_fields.add((id(frame), attr))

        self._apply_moved_artifact_paths(frames, moved_paths, moved_fields)

    def _realign_standard_frame_artifacts(
        self,
        task_id: str,
        frames: list[StoryboardFrame],
    ) -> None:
        """Move standard frame artifacts to match their new post-delete positions."""
        moved_paths: Dict[str, str] = {}
        moved_fields: set[tuple[int, str]] = set()

        for new_index, frame in enumerate(sorted(frames, key=lambda item: item.index)):
            old_index = frame.index
            if old_index == new_index:
                continue

            for attr, file_type in self._artifact_fields():
                current_path = getattr(frame, attr, None)
                if not current_path:
                    continue

                old_standard_path = self._standard_frame_artifact_path(
                    task_id,
                    old_index,
                    file_type,
                )
                if os.path.abspath(current_path) != os.path.abspath(old_standard_path):
                    continue

                new_standard_path = self._standard_frame_artifact_path(
                    task_id,
                    new_index,
                    file_type,
                )
                if os.path.exists(current_path):
                    os.makedirs(os.path.dirname(new_standard_path), exist_ok=True)
                    if os.path.exists(new_standard_path):
                        os.remove(new_standard_path)
                    shutil.move(current_path, new_standard_path)
                moved_paths[os.path.abspath(current_path)] = new_standard_path
                setattr(frame, attr, new_standard_path)
                moved_fields.add((id(frame), attr))

        self._apply_moved_artifact_paths(frames, moved_paths, moved_fields)

    def _remove_standard_frame_artifacts(self, task_id: str, frame_indexes: Iterable[int]) -> None:
        """Remove standard 01_* artifacts for frames that are being deleted."""
        for frame_index in frame_indexes:
            for _attr, file_type in self._artifact_fields():
                path = self._standard_frame_artifact_path(task_id, frame_index, file_type)
                try:
                    Path(path).unlink(missing_ok=True)
                except IsADirectoryError:
                    logger.warning(f"Skipped deleting directory artifact path: {path}")

    def _apply_moved_artifact_paths(
        self,
        frames: list[StoryboardFrame],
        moved_paths: Dict[str, str],
        moved_fields: set[tuple[int, str]],
    ) -> None:
        if not moved_paths:
            return

        for frame in frames:
            for attr, _file_type in self._artifact_fields():
                if (id(frame), attr) in moved_fields:
                    continue
                current_path = getattr(frame, attr, None)
                if not current_path:
                    continue
                moved_path = moved_paths.get(os.path.abspath(current_path))
                if moved_path:
                    setattr(frame, attr, moved_path)

    def _artifact_fields(self) -> tuple[tuple[str, str], ...]:
        return (
            ("audio_path", "audio"),
            ("image_path", "image"),
            ("video_path", "video"),
            ("composed_image_path", "composed"),
            ("video_segment_path", "segment"),
        )

    def _standard_frame_artifact_path(self, task_id: str, frame_index: int, file_type: str) -> str:
        ext_by_type = {
            "audio": "mp3",
            "image": "png",
            "video": "mp4",
            "composed": "png",
            "segment": "mp4",
        }
        frames_dir = self.persistence.get_task_dir(task_id) / "frames"
        return str(frames_dir / f"{frame_index + 1:02d}_{file_type}.{ext_by_type[file_type]}")

    def _template_requires_media(self, config: StoryboardConfig) -> bool:
        return get_template_type(os.path.basename(config.frame_template or "")) != "static"

    def _config_with_overrides(
        self,
        config: StoryboardConfig,
        overrides: Optional[Dict[str, Any]],
    ) -> StoryboardConfig:
        if not overrides:
            return copy.deepcopy(config)

        allowed = {field.name for field in fields(StoryboardConfig)}
        safe_overrides = {key: value for key, value in overrides.items() if key in allowed}
        return replace(copy.deepcopy(config), **safe_overrides)

    def _clear_audio_outputs(self, frame: StoryboardFrame) -> None:
        frame.audio_path = None
        frame.composed_image_path = None
        frame.video_segment_path = None
        frame.duration = 0.0

    def _clear_media_outputs(self, frame: StoryboardFrame) -> None:
        frame.media_type = None
        frame.image_path = None
        frame.video_path = None
        frame.composed_image_path = None
        frame.video_segment_path = None

    def _clear_template_outputs(self, frame: StoryboardFrame) -> None:
        frame.composed_image_path = None
        frame.video_segment_path = None

    def _ensure_compatible_template(self, old_template: str, new_template: str) -> None:
        old_type = get_template_type(os.path.basename(old_template or ""))
        new_type = get_template_type(os.path.basename(new_template or ""))
        if old_type != new_type:
            raise ValueError(
                "Template replacement only supports the same template type "
                f"for now: {old_type!r} -> {new_type!r}"
            )

    async def _process_frame(
        self,
        storyboard: Storyboard,
        frame: StoryboardFrame,
        config: StoryboardConfig,
        progress_callback: Optional[Callable[[ProgressEvent], None]] = None,
    ) -> None:
        await self.core.frame_processor(
            frame,
            storyboard,
            config,
            total_frames=len(storyboard.frames),
            progress_callback=progress_callback,
        )

    def _wrap_frame_progress(
        self,
        progress_callback: Optional[Callable[[ProgressEvent], None]],
        frame_offset: int,
        total_frames: int,
    ) -> Optional[Callable[[ProgressEvent], None]]:
        if progress_callback is None:
            return None

        def _callback(event: ProgressEvent) -> None:
            frame_progress = event.progress if event.progress is not None else 0.0
            overall = (frame_offset + frame_progress) / total_frames if total_frames else 1.0
            progress_callback(
                ProgressEvent(
                    event_type="editing_all_audio",
                    progress=max(0.0, min(overall, 1.0)),
                    frame_current=frame_offset + 1,
                    frame_total=total_frames,
                    step=event.step,
                    action=event.action,
                    extra_info=event.extra_info,
                )
            )

        return _callback

    async def _rebuild_final_video(
        self,
        task_id: str,
        storyboard: Storyboard,
        metadata: Dict[str, Any],
        bgm_path: Any = ...,
        bgm_volume: Optional[float] = None,
        bgm_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        segment_paths = [frame.video_segment_path for frame in storyboard.frames]
        missing = [str(i + 1) for i, path in enumerate(segment_paths) if not path or not os.path.exists(path)]
        if missing:
            raise ValueError(f"Cannot rebuild final video; missing segment(s): {', '.join(missing)}")

        input_params = metadata.get("input", {})
        if bgm_path is ...:
            bgm_path = input_params.get("bgm_path")
        if bgm_volume is None:
            bgm_volume = input_params.get("bgm_volume", 0.2)
        if bgm_mode is None:
            bgm_mode = input_params.get("bgm_mode", "loop")

        final_video_path = (
            storyboard.final_video_path
            or metadata.get("result", {}).get("video_path")
            or str(self.persistence.get_task_dir(task_id) / "final.mp4")
        )

        video_service = getattr(self.core, "video", None) or VideoService()
        video_service.concat_videos(
            videos=segment_paths,
            output=final_video_path,
            bgm_path=bgm_path,
            bgm_volume=bgm_volume,
            bgm_mode=bgm_mode,
        )

        storyboard.final_video_path = final_video_path
        storyboard.total_duration = sum(frame.duration or 0.0 for frame in storyboard.frames)
        storyboard.completed_at = datetime.now()

        result = metadata.setdefault("result", {})
        result["video_path"] = final_video_path
        result["duration"] = storyboard.total_duration
        result["n_frames"] = len(storyboard.frames)
        result["file_size"] = os.path.getsize(final_video_path) if os.path.exists(final_video_path) else 0
        metadata["status"] = "completed"
        metadata["completed_at"] = datetime.now().isoformat()

        await self.persistence.save_storyboard(task_id, storyboard)
        await self.persistence.save_task_metadata(task_id, metadata)

        return result
