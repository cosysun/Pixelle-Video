from typing import Optional

from .config import Config
from .tts_minimax import MiniMaxTTSClient


class TTSClient:
    """Unified third-party TTS client dispatcher."""

    def __init__(
        self,
        minimax_api_key: Optional[str] = None,
        minimax_base_url: Optional[str] = None,
        local_proxy: Optional[str] = None,
    ):
        proxy = local_proxy or Config.LOCAL_PROXY
        self.minimax_client = MiniMaxTTSClient(
            api_key=minimax_api_key or Config.MINIMAX_API_KEY,
            base_url=minimax_base_url or Config.MINIMAX_BASE_URL,
            local_proxy=proxy,
        )

    async def list_voices(self, provider: str = "minimax"):
        if provider != "minimax":
            raise ValueError(f"Unsupported TTS provider: {provider}")
        return await self.minimax_client.list_voices()

    async def generate_speech(
        self,
        provider: str,
        text: str,
        voice_id: str,
        model: str,
        output_path: str,
        speed: Optional[float] = None,
        volume: Optional[float] = None,
    ) -> str:
        if provider != "minimax":
            raise ValueError(f"Unsupported TTS provider: {provider}")
        return await self.minimax_client.generate_speech(
            text=text,
            voice_id=voice_id,
            model=model,
            output_path=output_path,
            speed=speed,
            volume=volume,
        )

    async def upload_voice_clone_audio(self, provider: str, audio_path: str) -> int:
        if provider != "minimax":
            raise ValueError(f"Unsupported TTS provider: {provider}")
        return await self.minimax_client.upload_voice_clone_audio(audio_path)

    async def clone_voice(self, provider: str, file_id: int, voice_id: str, **kwargs):
        if provider != "minimax":
            raise ValueError(f"Unsupported TTS provider: {provider}")
        return await self.minimax_client.clone_voice(file_id=file_id, voice_id=voice_id, **kwargs)
