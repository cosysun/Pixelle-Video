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
TTS API schemas
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class TTSSynthesizeRequest(BaseModel):
    """TTS synthesis request"""
    text: str = Field(..., description="Text to synthesize")
    inference_mode: Optional[Literal["local", "comfyui", "api"]] = Field(
        None,
        description="TTS inference mode. Use 'api' for MiniMax direct synthesis."
    )
    provider: Optional[str] = Field(None, description="Third-party TTS provider, e.g. 'minimax'")
    model: Optional[str] = Field(None, description="Third-party TTS model, e.g. 'speech-2.8-turbo'")
    workflow: Optional[str] = Field(
        None, 
        description="TTS workflow key (e.g., 'runninghub/tts_edge.json' or 'selfhost/tts_edge.json'). If not specified, uses default workflow from config."
    )
    ref_audio: Optional[str] = Field(
        None, 
        description="Reference audio path for voice cloning (optional). Can be a local file path or URL."
    )
    voice_id: Optional[str] = Field(
        None, 
        description="Voice ID. For API TTS mode this is the provider voice_id."
    )
    speed: Optional[float] = Field(None, ge=0.5, le=2.0, description="Speech speed multiplier")
    volume: Optional[float] = Field(None, ge=0.0, le=2.0, description="Speech volume multiplier")
    
    class Config:
        json_schema_extra = {
            "example": {
                "text": "Hello, welcome to Pixelle-Video!",
                "inference_mode": "api",
                "provider": "minimax",
                "model": "speech-2.8-turbo",
                "voice_id": "your_minimax_voice_id"
            }
        }


class TTSSynthesizeResponse(BaseModel):
    """TTS synthesis response"""
    success: bool = True
    message: str = "Success"
    audio_path: str = Field(..., description="Path to generated audio file")
    duration: float = Field(..., description="Audio duration in seconds")

