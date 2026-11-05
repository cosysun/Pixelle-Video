"""Legacy-compatible storyboard serialization, independent of storage."""

from datetime import datetime
from typing import Any, Dict

from pixelle_video.models.storyboard import (
    ContentMetadata,
    Storyboard,
    StoryboardConfig,
    StoryboardFrame,
)


class StoryboardSerializer:
    def _storyboard_to_dict(self, storyboard: Storyboard) -> Dict[str, Any]:
        """Convert Storyboard to dict for JSON serialization"""
        return {
            "title": storyboard.title,
            "config": self._config_to_dict(storyboard.config),
            "frames": [self._frame_to_dict(frame) for frame in storyboard.frames],
            "content_metadata": self._content_metadata_to_dict(storyboard.content_metadata)
            if storyboard.content_metadata
            else None,
            "final_video_path": storyboard.final_video_path,
            "total_duration": storyboard.total_duration,
            "created_at": storyboard.created_at.isoformat() if storyboard.created_at else None,
            "completed_at": storyboard.completed_at.isoformat()
            if storyboard.completed_at
            else None,
        }

    def _dict_to_storyboard(self, data: Dict[str, Any]) -> Storyboard:
        """Convert dict to Storyboard instance"""
        return Storyboard(
            title=data["title"],
            config=self._dict_to_config(data["config"]),
            frames=[self._dict_to_frame(frame_data) for frame_data in data["frames"]],
            content_metadata=self._dict_to_content_metadata(data["content_metadata"])
            if data.get("content_metadata")
            else None,
            final_video_path=data.get("final_video_path"),
            total_duration=data.get("total_duration", 0.0),
            created_at=datetime.fromisoformat(data["created_at"])
            if data.get("created_at")
            else None,
            completed_at=datetime.fromisoformat(data["completed_at"])
            if data.get("completed_at")
            else None,
        )

    def _config_to_dict(self, config: StoryboardConfig) -> Dict[str, Any]:
        """Convert StoryboardConfig to dict"""
        return {
            "task_id": config.task_id,
            "n_storyboard": config.n_storyboard,
            "min_narration_words": config.min_narration_words,
            "max_narration_words": config.max_narration_words,
            "min_image_prompt_words": config.min_image_prompt_words,
            "max_image_prompt_words": config.max_image_prompt_words,
            "video_fps": config.video_fps,
            "tts_inference_mode": config.tts_inference_mode,
            "voice_id": config.voice_id,
            "tts_workflow": config.tts_workflow,
            "tts_speed": config.tts_speed,
            "tts_volume": config.tts_volume,
            "tts_provider": config.tts_provider,
            "tts_model": config.tts_model,
            "tts_voice_id": config.tts_voice_id,
            "tts_audio_repair_enabled": config.tts_audio_repair_enabled,
            "ref_audio": config.ref_audio,
            "media_width": config.media_width,
            "media_height": config.media_height,
            "media_workflow": config.media_workflow,
            # Direct-API video provider params (duration, audio-driven flag,
            # first-frame workflow, etc). MUST be persisted so resume of an
            # API video task doesn't silently drop the provider config.
            "api_video_params": config.api_video_params,
            "frame_template": config.frame_template,
            "template_params": config.template_params,
        }

    def _dict_to_config(self, data: Dict[str, Any]) -> StoryboardConfig:
        """Convert dict to StoryboardConfig"""
        return StoryboardConfig(
            task_id=data.get("task_id"),
            n_storyboard=data.get("n_storyboard", 5),
            min_narration_words=data.get("min_narration_words", 5),
            max_narration_words=data.get("max_narration_words", 20),
            min_image_prompt_words=data.get("min_image_prompt_words", 30),
            max_image_prompt_words=data.get("max_image_prompt_words", 60),
            video_fps=data.get("video_fps", 30),
            tts_inference_mode=data.get("tts_inference_mode", "local"),
            voice_id=data.get("voice_id"),
            tts_workflow=data.get("tts_workflow"),
            tts_speed=data.get("tts_speed"),
            tts_volume=data.get("tts_volume"),
            tts_provider=data.get("tts_provider"),
            tts_model=data.get("tts_model"),
            tts_voice_id=data.get("tts_voice_id"),
            tts_audio_repair_enabled=data.get("tts_audio_repair_enabled", False),
            ref_audio=data.get("ref_audio"),
            media_width=data.get(
                "media_width", data.get("image_width", 1024)
            ),  # Backward compatibility
            media_height=data.get(
                "media_height", data.get("image_height", 1024)
            ),  # Backward compatibility
            media_workflow=data.get(
                "media_workflow", data.get("image_workflow")
            ),  # Backward compatibility
            api_video_params=data.get("api_video_params"),
            frame_template=data.get("frame_template", "1080x1920/default.html"),
            template_params=data.get("template_params"),
        )

    def _frame_to_dict(self, frame: StoryboardFrame) -> Dict[str, Any]:
        """Convert StoryboardFrame to dict"""
        return {
            "index": frame.index,
            "narration": frame.narration,
            "image_prompt": frame.image_prompt,
            "audio_path": frame.audio_path,
            "media_type": frame.media_type,
            "image_path": frame.image_path,
            "video_path": frame.video_path,
            "composed_image_path": frame.composed_image_path,
            "video_segment_path": frame.video_segment_path,
            "image_source_index": frame.image_source_index,
            "duration": frame.duration,
            "created_at": frame.created_at.isoformat() if frame.created_at else None,
        }

    def _dict_to_frame(self, data: Dict[str, Any]) -> StoryboardFrame:
        """Convert dict to StoryboardFrame"""
        return StoryboardFrame(
            index=data["index"],
            narration=data["narration"],
            image_prompt=data["image_prompt"],
            audio_path=data.get("audio_path"),
            media_type=data.get("media_type"),
            image_path=data.get("image_path"),
            video_path=data.get("video_path"),
            composed_image_path=data.get("composed_image_path"),
            video_segment_path=data.get("video_segment_path"),
            image_source_index=data.get("image_source_index"),
            duration=data.get("duration", 0.0),
            created_at=datetime.fromisoformat(data["created_at"])
            if data.get("created_at")
            else None,
        )

    def _content_metadata_to_dict(self, metadata: ContentMetadata) -> Dict[str, Any]:
        """Convert ContentMetadata to dict"""
        return {
            "title": metadata.title,
            "author": metadata.author,
            "subtitle": metadata.subtitle,
            "genre": metadata.genre,
            "summary": metadata.summary,
            "publication_year": metadata.publication_year,
            "cover_url": metadata.cover_url,
        }

    def _dict_to_content_metadata(self, data: Dict[str, Any]) -> ContentMetadata:
        """Convert dict to ContentMetadata"""
        return ContentMetadata(
            title=data["title"],
            author=data.get("author"),
            subtitle=data.get("subtitle"),
            genre=data.get("genre"),
            summary=data.get("summary"),
            publication_year=data.get("publication_year"),
            cover_url=data.get("cover_url"),
        )

    # ========================================================================
