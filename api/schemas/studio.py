"""Public studio contracts use registered resources, never local file paths."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=512)


class CreateVideoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=100000)
    mode: Literal["generate", "fixed"] = "generate"
    title: str | None = Field(None, max_length=200)
    n_scenes: int = Field(5, ge=3, le=30)
    split_mode: Literal["paragraph", "line", "sentence"] = "paragraph"
    content_style: Literal["general", "tech_pop"] = "general"
    min_narration_words: int = Field(5, ge=1, le=100)
    max_narration_words: int = Field(20, ge=1, le=200)
    template_id: str
    media_workflow: str | None = None
    prompt_prefix: str = Field("", max_length=4000)
    tts_inference_mode: Literal["local", "api"] = "local"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    tts_provider: Literal["minimax"] = "minimax"
    tts_model: str = "speech-2.8-turbo"
    tts_voice_id: str | None = None
    tts_speed: float = Field(1, ge=0.5, le=2)
    tts_volume: float = Field(1, ge=0, le=2)
    bgm_id: str | None = None
    bgm_volume: float = Field(0.2, ge=0, le=1)
    video_fps: int = Field(30, ge=15, le=60)
    video_duration: int = Field(5, ge=2, le=15)
    first_frame_workflow: str | None = None
    use_narration_audio_as_driving_audio: bool = False
    template_params: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_content(self):
        if not self.text.strip():
            raise ValueError("请输入主题或文案")
        if self.max_narration_words < self.min_narration_words:
            raise ValueError("文案最大字数不能小于最小字数")
        for value in (self.media_workflow, self.first_frame_workflow):
            if value and not value.startswith("api/"):
                raise ValueError("仅支持直连 API 模型")
        if self.tts_inference_mode == "api" and not self.tts_voice_id:
            raise ValueError("请选择 API 配音音色")
        return self

    def generation_params(self):
        values = self.model_dump()
        duration = values.pop("video_duration")
        first = values.pop("first_frame_workflow")
        audio = values.pop("use_narration_audio_as_driving_audio")
        values["api_video_params"] = {
            "duration": duration,
            "first_frame_workflow": first,
            "use_narration_audio_as_driving_audio": audio,
        }
        return values


class BatchRequest(BaseModel):
    topics: list[str] = Field(min_length=1, max_length=100)
    settings: CreateVideoRequest


class TTSOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tts_inference_mode: Literal["local", "api"] | None = None
    voice_id: str | None = None
    tts_voice_id: str | None = None
    tts_provider: Literal["minimax"] | None = None
    tts_model: str | None = Field(None, max_length=100)
    tts_speed: float | None = Field(None, ge=0.5, le=2)
    tts_volume: float | None = Field(None, ge=0, le=2)


class MediaOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_workflow: str | None = None

    @model_validator(mode="after")
    def direct_model(self):
        if self.media_workflow and not self.media_workflow.startswith("api/"):
            raise ValueError("仅支持直连 API 模型")
        return self


class EditValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, min_length=1, max_length=200)
    bgm_id: str | None = None
    bgm_volume: float | None = Field(None, ge=0, le=1)
    bgm_mode: Literal["loop", "once"] | None = None
    tts_overrides: TTSOverrides | None = None
    media_overrides: MediaOverrides | None = None
    frame_index: int | None = Field(None, ge=0)
    narration: str | None = Field(None, min_length=1, max_length=5000)
    image_prompt: str | None = Field(None, max_length=5000)
    persist_overrides: bool | None = None
    position: int | None = Field(None, ge=0)
    image_source_index: int | None = Field(None, ge=0)
    template_id: str | None = None


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "update_title",
        "update_bgm",
        "remove_bgm",
        "regenerate_all_audio",
        "regenerate_frame_audio",
        "regenerate_frame_media",
        "insert_frame",
        "delete_frame",
        "replace_template",
        "recompose",
    ]
    values: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_values(self):
        allowed = {
            "update_title": {"title"},
            "update_bgm": {"bgm_id", "bgm_volume", "bgm_mode"},
            "remove_bgm": set(),
            "regenerate_all_audio": {"tts_overrides"},
            "regenerate_frame_audio": {
                "frame_index",
                "narration",
                "tts_overrides",
                "persist_overrides",
            },
            "regenerate_frame_media": {
                "frame_index",
                "image_prompt",
                "media_overrides",
                "persist_overrides",
            },
            "insert_frame": {
                "position",
                "narration",
                "image_prompt",
                "image_source_index",
                "tts_overrides",
                "media_overrides",
                "persist_overrides",
            },
            "delete_frame": {"frame_index"},
            "replace_template": {"template_id"},
            "recompose": set(),
        }
        required = {
            "update_title": {"title"},
            "regenerate_frame_audio": {"frame_index", "narration"},
            "regenerate_frame_media": {"frame_index", "image_prompt"},
            "insert_frame": {"position", "narration"},
            "delete_frame": {"frame_index"},
            "replace_template": {"template_id"},
        }
        if set(self.values) - allowed[self.action]:
            raise ValueError("此操作包含不支持的参数")
        if any(self.values.get(k) is None for k in required.get(self.action, set())):
            raise ValueError("编辑参数不完整")
        validated = EditValues(**self.values)
        self.values = validated.model_dump(include=set(self.values), exclude_unset=True)
        return self


class UploadRequest(BaseModel):
    kind: Literal["bgm", "image"]
    name: str = Field(min_length=1, max_length=160)
    size: int = Field(gt=0, le=100 * 1024 * 1024)
    content_type: Literal["audio/mpeg", "audio/wav", "image/png", "image/jpeg", "image/webp"]


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_workflow: str | None = None
    tts_inference_mode: Literal["local", "api"] | None = None
    tts_voice: str | None = None
    tts_voice_id: str | None = None
    tts_model: str | None = None
    template_id: str | None = None
    first_frame_workflow: str | None = None

    @model_validator(mode="after")
    def direct_models(self):
        for model in (self.media_workflow, self.first_frame_workflow):
            if model and not model.startswith("api/"):
                raise ValueError("请选择直连 API 模型")
        return self
