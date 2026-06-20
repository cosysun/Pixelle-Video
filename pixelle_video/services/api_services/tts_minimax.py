import os
from pathlib import Path
from typing import Any, Optional

import httpx


class MiniMaxTTSClient:
    """MiniMax speech client for voice listing, cloning, and T2A synthesis."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.minimaxi.com",
        local_proxy: Optional[str] = None,
    ):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "https://api.minimaxi.com").rstrip("/")
        self.local_proxy = local_proxy

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError("MiniMax API key is not configured")
        return {"Authorization": f"Bearer {self.api_key}"}

    def _client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"timeout": 120}
        if self.local_proxy:
            kwargs["proxy"] = self.local_proxy
        return kwargs

    @staticmethod
    def _check_base_resp(payload: dict[str, Any]):
        base_resp = payload.get("base_resp") or {}
        status_code = base_resp.get("status_code", 0)
        if status_code != 0:
            status_msg = base_resp.get("status_msg") or "Unknown error"
            raise RuntimeError(f"MiniMax API error {status_code}: {status_msg}")

    @staticmethod
    def _normalize_voices(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        system = []
        for item in payload.get("system_voice") or []:
            voice_id = item.get("voice_id", "")
            voice_name = item.get("voice_name") or voice_id
            system.append({
                "voice_id": voice_id,
                "display_name": voice_name,
                "description": item.get("description") or [],
                "type": "system",
            })

        cloning = []
        for item in payload.get("voice_cloning") or []:
            voice_id = item.get("voice_id", "")
            cloning.append({
                "voice_id": voice_id,
                "display_name": voice_id,
                "description": item.get("description") or [],
                "created_time": item.get("created_time"),
                "type": "voice_cloning",
            })

        generation = []
        for item in payload.get("voice_generation") or []:
            voice_id = item.get("voice_id", "")
            generation.append({
                "voice_id": voice_id,
                "display_name": voice_id,
                "description": item.get("description") or [],
                "created_time": item.get("created_time"),
                "type": "voice_generation",
            })

        return {
            "system": system,
            "voice_cloning": cloning,
            "voice_generation": generation,
        }

    async def list_voices(self, voice_type: str = "all") -> dict[str, list[dict[str, Any]]]:
        """Fetch available MiniMax voice IDs grouped by type."""
        async with httpx.AsyncClient(**self._client_kwargs()) as client:
            response = await client.post(
                f"{self.base_url}/v1/get_voice",
                headers=self._headers(),
                json={"voice_type": voice_type},
            )
            response.raise_for_status()
            payload = response.json()

        self._check_base_resp(payload)
        return self._normalize_voices(payload)

    async def upload_voice_clone_audio(self, audio_path: str) -> int:
        """Upload reference audio for MiniMax voice cloning."""
        path = Path(audio_path)
        async with httpx.AsyncClient(**self._client_kwargs()) as client:
            with path.open("rb") as file_obj:
                response = await client.post(
                    f"{self.base_url}/v1/files/upload",
                    headers=self._headers(),
                    data={"purpose": "voice_clone"},
                    files={"file": (path.name, file_obj)},
                )
            response.raise_for_status()
            payload = response.json()

        self._check_base_resp(payload)
        file_info = payload.get("file") or {}
        file_id = file_info.get("file_id")
        if file_id is None:
            raise RuntimeError("MiniMax upload response missing file_id")
        return int(file_id)

    async def clone_voice(
        self,
        file_id: int,
        voice_id: str,
        *,
        text: Optional[str] = None,
        model: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a MiniMax cloned voice. The caller provides the final voice_id."""
        body: dict[str, Any] = {"file_id": file_id, "voice_id": voice_id}
        if text:
            body["text"] = text
            body["model"] = model or "speech-2.8-turbo"

        async with httpx.AsyncClient(**self._client_kwargs()) as client:
            response = await client.post(
                f"{self.base_url}/v1/voice_clone",
                headers=self._headers(),
                json=body,
            )
            response.raise_for_status()
            payload = response.json()

        self._check_base_resp(payload)
        return payload

    async def generate_speech(
        self,
        text: str,
        voice_id: str,
        model: str,
        output_path: str,
        speed: Optional[float] = None,
        volume: Optional[float] = None,
    ) -> str:
        """Generate non-streaming T2A audio and write it to output_path."""
        voice_setting: dict[str, Any] = {"voice_id": voice_id}
        if speed is not None:
            voice_setting["speed"] = speed
        if volume is not None:
            voice_setting["vol"] = volume

        body = {
            "model": model,
            "text": text,
            "stream": False,
            "voice_setting": voice_setting,
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
        }

        async with httpx.AsyncClient(**self._client_kwargs()) as client:
            response = await client.post(
                f"{self.base_url}/v1/t2a_v2",
                headers=self._headers(),
                json=body,
            )
            response.raise_for_status()
            payload = response.json()

        self._check_base_resp(payload)
        audio_hex = (payload.get("data") or {}).get("audio")
        if not audio_hex:
            raise RuntimeError("MiniMax T2A response missing audio data")

        try:
            audio_bytes = bytes.fromhex(audio_hex)
        except ValueError as exc:
            raise RuntimeError("MiniMax T2A returned invalid hex audio") from exc

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(audio_bytes)
        return output_path
