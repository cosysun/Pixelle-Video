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
Session state management for web UI
"""

import hashlib
import inspect
import json

import streamlit as st
from loguru import logger

from web.i18n import get_language, set_language
from web.utils.async_helpers import run_async

_PIXELLE_CORE_CAPABILITY_VERSION = 6


def clear_history_frame_edit_state(state, task_id: str) -> None:
    """Clear frame-indexed history edit widgets for one task.

    Streamlit widget state is keyed by frame index. After insert/delete changes
    indexes, stale values must be dropped so widgets reload from storyboard.json.
    """
    prefixes = (
        f"edit_narration_{task_id}_",
        f"edit_prompt_{task_id}_",
        f"frame_audio_{task_id}_",
        f"frame_media_{task_id}_",
        f"regenerate_audio_{task_id}_",
        f"regenerate_media_{task_id}_",
        f"delete_frame_confirm_{task_id}_",
        f"delete_frame_submit_{task_id}_",
        f"insert_narration_{task_id}_",
        f"insert_prompt_{task_id}_",
        f"insert_audio_{task_id}_",
        f"insert_media_{task_id}_",
        f"insert_frame_submit_{task_id}_",
    )
    for key in list(state.keys()):
        if key.startswith(prefixes):
            del state[key]


def init_session_state():
    """Initialize session state variables"""
    if "language" not in st.session_state:
        # Use auto-detected system language
        st.session_state.language = get_language()


def init_i18n():
    """Initialize internationalization"""
    # Locales are already loaded and system language detected on import
    # Get language from session state or use auto-detected system language
    if "language" not in st.session_state:
        st.session_state.language = get_language()  # Use auto-detected language
    
    # Set current language
    set_language(st.session_state.language)


def _needs_core_recreate(pixelle_video) -> bool:
    """Return True when cached core predates capabilities required by current UI."""
    required_history_methods = (
        "remove_bgm",
        "update_bgm",
        "update_title",
        "regenerate_all_audio",
        "regenerate_frame_audio",
        "regenerate_frame_media",
        "insert_frame",
        "delete_frame",
        "replace_template",
        "request_cancel_edit",
    )
    history = getattr(pixelle_video, "history", None)
    if history is None:
        return True
    if getattr(pixelle_video, "task_editor", None) is None:
        return True
    tts_service = getattr(pixelle_video, "tts", None)
    if tts_service is None or not hasattr(tts_service, "_call_api_tts"):
        return True
    if any(not hasattr(history, method) for method in required_history_methods):
        return True

    try:
        signature = inspect.signature(history.regenerate_all_audio)
    except (TypeError, ValueError):
        return True
    return "progress_callback" not in signature.parameters


def get_pixelle_video():
    """
    Get initialized Pixelle-Video instance with proper caching and cleanup
    
    Uses st.session_state to cache the instance per user session.
    ComfyKit is lazily initialized and automatically recreated on config changes.
    """
    from pixelle_video.config import config_manager
    from pixelle_video.service import PixelleVideoCore
    
    # Compute config hash for change detection
    config_dict = config_manager.config.to_dict()
    # Only track ComfyUI config for hash (other config changes don't need core recreation)
    comfyui_config = config_dict.get("comfyui", {})
    config_hash = hashlib.md5(json.dumps(comfyui_config, sort_keys=True).encode()).hexdigest()
    
    # Check if we need to create or recreate core instance
    need_recreate = False
    if 'pixelle_video' not in st.session_state:
        need_recreate = True
        logger.info("Creating new PixelleVideoCore instance (first time)")
    elif st.session_state.get('pixelle_video_config_hash') != config_hash:
        need_recreate = True
        logger.info("Configuration changed, recreating PixelleVideoCore instance")
        # Cleanup old instance
        old_core = st.session_state.pixelle_video
        try:
            run_async(old_core.cleanup())
        except Exception as e:
            logger.warning(f"Failed to cleanup old PixelleVideoCore: {e}")
    elif _needs_core_recreate(st.session_state.pixelle_video):
        need_recreate = True
        logger.info("Cached PixelleVideoCore is missing new editing services, recreating")
        old_core = st.session_state.pixelle_video
        try:
            run_async(old_core.cleanup())
        except Exception as e:
            logger.warning(f"Failed to cleanup old PixelleVideoCore: {e}")
    elif st.session_state.get("pixelle_video_capability_version") != _PIXELLE_CORE_CAPABILITY_VERSION:
        need_recreate = True
        logger.info("PixelleVideoCore capability version changed, recreating")
        old_core = st.session_state.pixelle_video
        try:
            run_async(old_core.cleanup())
        except Exception as e:
            logger.warning(f"Failed to cleanup old PixelleVideoCore: {e}")
    
    if need_recreate:
        # Create and initialize new instance
        pixelle_video = PixelleVideoCore()
        run_async(pixelle_video.initialize())
        
        # Cache in session state
        st.session_state.pixelle_video = pixelle_video
        st.session_state.pixelle_video_config_hash = config_hash
        st.session_state.pixelle_video_capability_version = _PIXELLE_CORE_CAPABILITY_VERSION
        logger.info("✅ PixelleVideoCore initialized and cached")
    else:
        pixelle_video = st.session_state.pixelle_video
        logger.debug("Reusing cached PixelleVideoCore instance")
    
    return pixelle_video

