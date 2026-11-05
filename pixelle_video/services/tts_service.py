"""Speech synthesis via Edge TTS and direct provider APIs."""

import uuid
from pathlib import Path
from typing import Optional

from aiohttp import ClientResponseError, WSServerHandshakeError
from loguru import logger

from pixelle_video.config import config_manager
from pixelle_video.services.api_services.tts_client import TTSClient
from pixelle_video.tts_voices import speed_to_rate
from pixelle_video.utils.tts_util import edge_tts


class TTSService:
    def __init__(self, config, core=None):
        self.full_config = config
        self.core = core

    @property
    def config(self):
        return config_manager.config.tts.model_dump()

    def client(self):
        provider = config_manager.config.tts_models.providers.minimax
        common = config_manager.config.api_providers.common
        return TTSClient(
            minimax_api_key=provider.api_key,
            minimax_base_url=provider.base_url,
            local_proxy=common.local_proxy if provider.use_proxy else "",
        )

    async def __call__(
        self, text, voice=None, speed=None, inference_mode=None, output_path=None, **params
    ):
        mode = inference_mode or self.config["inference_mode"]
        if mode == "local":
            return await self._call_local_tts(
                text, voice=voice, speed=speed, output_path=output_path
            )
        if mode == "api":
            return await self._call_api_tts(
                text,
                voice_id=params.pop("voice_id", None) or voice,
                speed=speed,
                output_path=output_path,
                **params,
            )
        raise ValueError("该作品使用已移除的配音服务，请选择 Edge TTS 或 API 配音")

    async def _call_api_tts(
        self,
        text: str,
        voice_id: Optional[str] = None,
        speed: Optional[float] = None,
        output_path: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        volume: Optional[float] = None,
        **params,
    ) -> str:
        """Generate speech using a direct third-party TTS provider."""
        try:
            tts_models_config = config_manager.config.to_dict().get("tts_models", {})
        except Exception:
            tts_models_config = self.full_config.get("tts_models", {})
        providers_config = tts_models_config.get("providers", {})
        final_provider = provider or tts_models_config.get("default_provider", "minimax")
        provider_config = providers_config.get(final_provider, {})
        final_model = (
            model
            or provider_config.get("default_model")
            or tts_models_config.get("default_model")
            or "speech-2.8-turbo"
        )
        final_volume = volume

        if not voice_id:
            raise ValueError("voice_id is required for API TTS mode")

        if not output_path:
            unique_id = uuid.uuid4().hex
            output_path = str(
                Path(
                    __import__(
                        "pixelle_video.utils.os_util", fromlist=["get_temp_path"]
                    ).get_temp_path()
                )
                / f"{unique_id}.mp3"
            )
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"🎙️  Using API TTS: provider={final_provider}, model={final_model}, voice_id={voice_id}"
        )
        client = self.client()
        return await client.generate_speech(
            provider=final_provider,
            text=text,
            voice_id=voice_id,
            model=final_model,
            output_path=output_path,
            speed=speed,
            volume=final_volume,
        )

    async def _call_local_tts(
        self,
        text: str,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
        output_path: Optional[str] = None,
    ) -> str:
        """
        Generate speech using local Edge TTS

        Args:
            text: Text to convert to speech
            voice: Edge TTS voice ID (default: from config)
            speed: Speech speed multiplier (default: from config)
            output_path: Custom output path (auto-generated if None)

        Returns:
            Generated audio file path
        """
        # Get config defaults
        local_config = self.config.get("local", {})

        # Determine voice and speed (param > config)
        final_voice = voice or local_config.get("voice", "zh-CN-YunjianNeural")
        final_speed = speed if speed is not None else local_config.get("speed", 1.2)

        # Convert speed to rate parameter
        rate = speed_to_rate(final_speed)

        logger.info(
            f"🎙️  Using local Edge TTS: voice={final_voice}, speed={final_speed}x (rate={rate})"
        )

        # Generate output path if not provided
        if not output_path:
            # Generate unique filename
            unique_id = uuid.uuid4().hex
            output_path = str(
                Path(
                    __import__(
                        "pixelle_video.utils.os_util", fromlist=["get_temp_path"]
                    ).get_temp_path()
                )
                / f"{unique_id}.mp3"
            )

            # Ensure output directory exists
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # Call Edge TTS
        try:
            await edge_tts(text=text, voice=final_voice, rate=rate, output_path=output_path)

            logger.info(f"✅ Generated audio (local Edge TTS): {output_path}")
            return output_path

        except (WSServerHandshakeError, ClientResponseError) as e:
            if e.status in {401, 403}:
                raise ValueError("Edge 配音服务暂时拒绝访问，请选择 MiniMax 配音或稍后重试") from e
            raise
        except Exception as e:
            logger.error(f"Local TTS generation error: {e}")
            raise
