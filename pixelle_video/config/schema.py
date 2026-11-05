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
Configuration schema with Pydantic models

Single source of truth for all configuration defaults and validation.
"""

from typing import Optional

from pydantic import BaseModel, Field, model_validator


class LLMConfig(BaseModel):
    """LLM configuration"""

    api_key: str = Field(default="", description="LLM API Key")
    base_url: str = Field(default="", description="LLM API Base URL")
    model: str = Field(default="", description="LLM Model Name")


class APIProviderCommonConfig(BaseModel):
    """Common API provider settings"""

    print_model_input: bool = Field(
        default=False, description="Print provider request parameters for debugging"
    )
    local_proxy: str = Field(default="", description="Local HTTP proxy for providers that need it")


class APIKeyProviderConfig(BaseModel):
    """Provider settings with API key and optional base URL"""

    api_key: str = Field(default="", description="Provider API Key")
    base_url: str = Field(default="", description="Provider API Base URL")
    use_proxy: bool = Field(
        default=False, description="Route provider requests through common local proxy"
    )


class AccessSecretProviderConfig(BaseModel):
    """Provider settings with access key / secret key credentials"""

    base_url: str = Field(default="", description="Provider API Base URL")
    access_key: str = Field(default="", description="Provider Access Key")
    secret_key: str = Field(default="", description="Provider Secret Key")
    use_proxy: bool = Field(
        default=False, description="Route provider requests through common local proxy"
    )


class APIProvidersConfig(BaseModel):
    """Direct model provider API configuration"""

    common: APIProviderCommonConfig = Field(default_factory=APIProviderCommonConfig)
    openai: APIKeyProviderConfig = Field(default_factory=APIKeyProviderConfig)
    openrouter: APIKeyProviderConfig = Field(default_factory=APIKeyProviderConfig)
    dashscope: APIKeyProviderConfig = Field(default_factory=APIKeyProviderConfig)
    deepseek: APIKeyProviderConfig = Field(default_factory=APIKeyProviderConfig)
    gemini: APIKeyProviderConfig = Field(default_factory=APIKeyProviderConfig)
    ark: APIKeyProviderConfig = Field(default_factory=APIKeyProviderConfig)
    hunyuan: APIKeyProviderConfig = Field(default_factory=APIKeyProviderConfig)
    kling: AccessSecretProviderConfig = Field(default_factory=AccessSecretProviderConfig)


class MiniMaxTTSProviderConfig(APIKeyProviderConfig):
    """MiniMax TTS provider settings"""

    base_url: str = Field(default="https://api.minimaxi.com", description="MiniMax API Base URL")
    default_model: str = Field(default="speech-2.8-turbo", description="Default MiniMax TTS model")
    default_voice_id: Optional[str] = Field(default=None, description="Default MiniMax voice_id")
    default_voice_type: Optional[str] = Field(
        default=None, description="Default MiniMax voice type"
    )


class TTSModelProvidersConfig(BaseModel):
    """Third-party TTS model provider settings"""

    minimax: MiniMaxTTSProviderConfig = Field(default_factory=MiniMaxTTSProviderConfig)


class TTSModelsConfig(BaseModel):
    """Third-party TTS model configuration"""

    default_provider: str = Field(default="minimax", description="Default third-party TTS provider")
    default_model: str = Field(
        default="speech-2.8-turbo", description="Default third-party TTS model"
    )
    providers: TTSModelProvidersConfig = Field(default_factory=TTSModelProvidersConfig)


class TTSLocalConfig(BaseModel):
    """Local TTS configuration (Edge TTS)"""

    voice: str = Field(default="zh-CN-YunjianNeural", description="Edge TTS voice ID")
    speed: float = Field(
        default=1.2, ge=0.5, le=2.0, description="Speech speed multiplier (0.5-2.0)"
    )


class TTSSubConfig(BaseModel):
    inference_mode: str = Field(default="local", pattern="^(local|api)$")
    local: TTSLocalConfig = Field(default_factory=TTSLocalConfig)


class ImageSubConfig(BaseModel):
    """Image-specific configuration (direct API image defaults)"""

    default_workflow: Optional[str] = Field(
        default="api/dashscope/wan2.7-image",
        description="Default image workflow (optional)",
    )
    prompt_prefix: str = Field(
        default=(
            "Minimalist black and white sketch style with muted watercolor accents, "
            "colored pencil elements, expressive matchstick figure interacting with colorful "
            "conceptual objects, loose clean lines, storybook illustration, soft lighting"
        ),
        description="Prompt prefix for all image generation",
    )


class VideoSubConfig(BaseModel):
    """Video-specific configuration (direct API video defaults)"""

    default_workflow: Optional[str] = Field(
        default=None, description="Default video workflow (optional)"
    )
    prompt_prefix: str = Field(
        default=(
            "Minimalist black and white sketch style with muted watercolor accents, "
            "colored pencil elements, expressive matchstick figure interacting with colorful "
            "conceptual objects, loose clean lines, storybook illustration, soft lighting"
        ),
        description="Prompt prefix for all video generation",
    )


class MediaConfig(BaseModel):
    image: ImageSubConfig = Field(default_factory=ImageSubConfig)
    video: VideoSubConfig = Field(default_factory=VideoSubConfig)


class TemplateConfig(BaseModel):
    """Template configuration"""

    default_template: str = Field(
        default="1080x1920/image_default.html", description="Default frame template path"
    )


class PixelleVideoConfig(BaseModel):
    """Pixelle-Video main configuration"""

    project_name: str = Field(default="Pixelle-Video", description="Project name")
    llm: LLMConfig = Field(default_factory=LLMConfig)
    api_providers: APIProvidersConfig = Field(default_factory=APIProvidersConfig)
    tts_models: TTSModelsConfig = Field(default_factory=TTSModelsConfig)
    tts: TTSSubConfig = Field(default_factory=TTSSubConfig)
    media: MediaConfig = Field(default_factory=MediaConfig)
    template: TemplateConfig = Field(default_factory=TemplateConfig)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        legacy = value.pop("comfyui", {})
        if legacy:
            old_tts = legacy.get("tts", {})
            value.setdefault(
                "tts",
                {
                    "inference_mode": old_tts.get("inference_mode")
                    if old_tts.get("inference_mode") in {"local", "api"}
                    else "local",
                    "local": old_tts.get("local", {}),
                },
            )
            value.setdefault(
                "media", {"image": legacy.get("image", {}), "video": legacy.get("video", {})}
            )
        for section in (value.get("media") or {}).values():
            if (
                isinstance(section, dict)
                and section.get("default_workflow")
                and not section["default_workflow"].startswith("api/")
            ):
                section["default_workflow"] = None
        return value

    def is_llm_configured(self) -> bool:
        """Check if LLM is properly configured"""
        return bool(
            self.llm.api_key
            and self.llm.api_key.strip()
            and self.llm.base_url
            and self.llm.base_url.strip()
            and self.llm.model
            and self.llm.model.strip()
        )

    def validate_required(self) -> bool:
        """Validate required configuration"""
        return self.is_llm_configured()

    def to_dict(self) -> dict:
        """Convert to dictionary (for backward compatibility)"""
        return self.model_dump()
