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
History Page - View generation history and manage tasks
"""

import importlib
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root to sys.path
_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import streamlit as st  # noqa: E402
from loguru import logger  # noqa: E402

from pixelle_video.tts_voices import EDGE_TTS_VOICES, get_voice_display_name  # noqa: E402
from web.components.header import render_header  # noqa: E402
from web.i18n import get_language, tr  # noqa: E402
from web.state.session import (  # noqa: E402
    clear_history_frame_edit_state,
    get_pixelle_video,
    init_i18n,
    init_session_state,
)
from web.utils.async_helpers import run_async  # noqa: E402
from web.utils.edit_action_guard import is_recent_duplicate_action  # noqa: E402
from web.utils.tts_models_config import (  # noqa: E402
    get_tts_models_config,
    resolve_history_api_tts_voice_id,
)
from web.utils.video_preview import read_video_preview_bytes  # noqa: E402

# Page config
st.set_page_config(
    page_title="History - Pixelle-Video",
    page_icon="📚",
    layout="wide",
)


def format_duration(seconds: float) -> str:
    """Format duration in seconds to readable string"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        hours = int(seconds / 3600)
        minutes = int((seconds % 3600) / 60)
        return f"{hours}h {minutes}m"


def format_file_size(bytes_size: int) -> str:
    """Format file size in bytes to readable string"""
    if bytes_size < 1024:
        return f"{bytes_size}B"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.1f}KB"
    elif bytes_size < 1024 * 1024 * 1024:
        return f"{bytes_size / 1024 / 1024:.1f}MB"
    else:
        return f"{bytes_size / 1024 / 1024 / 1024:.2f}GB"


def format_datetime(iso_string: str) -> str:
    """Format ISO datetime string to readable format"""
    try:
        dt = datetime.fromisoformat(iso_string)
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return iso_string


def _render_local_video(video_path: str, **kwargs):
    """Render local video bytes so overwritten files are not served from cache."""
    st.video(read_video_preview_bytes(video_path), format="video/mp4", **kwargs)


def truncate_text(text: str, max_length: int = 60) -> str:
    """Truncate text to max length"""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


def _workflow_options(service, current_key: str | None = None, media_type: str | None = None):
    """Return display options and workflow metadata for a workflow-capable service."""
    workflows = []
    if service is not None:
        try:
            workflows = service.list_workflows()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to list workflows: {e}")

    if media_type:
        workflows = [
            wf for wf in workflows
            if wf.get("media_type") in (None, media_type)
            or (media_type == "image" and str(wf.get("key", "")).split("/")[-1].startswith("image_"))
            or (media_type == "video" and str(wf.get("key", "")).split("/")[-1].startswith("video_"))
        ]

    workflow_by_key = {wf["key"]: wf for wf in workflows if wf.get("key")}
    if current_key and current_key not in workflow_by_key:
        workflow_by_key[current_key] = {"key": current_key, "display_name": current_key}

    options = [wf.get("display_name") or wf["key"] for wf in workflow_by_key.values()]
    keys = [wf["key"] for wf in workflow_by_key.values()]
    return options, keys


def _render_tts_overrides(prefix: str, pixelle_video, config) -> tuple[dict, bool]:
    """Render compact TTS override controls for history editing."""
    current_mode = getattr(config, "tts_inference_mode", None) or "local"
    mode_options = ["local", "comfyui", "api"]
    tts_mode = st.radio(
        tr("history.edit.tts_mode"),
        mode_options,
        horizontal=True,
        index=mode_options.index(current_mode) if current_mode in mode_options else 0,
        format_func=lambda x: tr(f"tts.mode.{x}"),
        key=f"{prefix}_tts_mode",
    )

    overrides = {"tts_inference_mode": tts_mode}
    if tts_mode == "local":
        voice_ids = [voice["id"] for voice in EDGE_TTS_VOICES]
        current_voice = getattr(config, "voice_id", None) or "zh-CN-YunjianNeural"
        if current_voice not in voice_ids:
            voice_ids.insert(0, current_voice)
        voice_options = [
            get_voice_display_name(voice_id, tr, get_language())
            for voice_id in voice_ids
        ]
        voice_display = st.selectbox(
            tr("history.edit.voice"),
            voice_options,
            index=voice_ids.index(current_voice),
            key=f"{prefix}_voice",
        )
        overrides["voice_id"] = voice_ids[voice_options.index(voice_display)]
        overrides["tts_workflow"] = None
        overrides["ref_audio"] = None
        overrides["tts_speed"] = st.slider(
            tr("history.edit.tts_speed"),
            min_value=0.5,
            max_value=2.0,
            value=float(getattr(config, "tts_speed", None) or 1.2),
            step=0.1,
            format="%.1fx",
            key=f"{prefix}_tts_speed",
        )
        overrides["tts_provider"] = None
        overrides["tts_model"] = None
        overrides["tts_voice_id"] = None
        overrides["tts_volume"] = None
    elif tts_mode == "comfyui":
        current_workflow = getattr(config, "tts_workflow", None) or ""
        workflow_options, workflow_keys = _workflow_options(
            getattr(pixelle_video, "tts", None),
            current_workflow,
        )
        if workflow_options:
            default_index = workflow_keys.index(current_workflow) if current_workflow in workflow_keys else 0
            selected_workflow_display = st.selectbox(
                tr("history.edit.tts_workflow"),
                workflow_options,
                index=default_index,
                key=f"{prefix}_tts_workflow_select",
                help=tr("history.edit.workflow_help"),
            )
            workflow_value = workflow_keys[workflow_options.index(selected_workflow_display)]
        else:
            workflow_value = st.text_input(
                tr("history.edit.tts_workflow"),
                value=current_workflow,
                key=f"{prefix}_tts_workflow",
                help=tr("history.edit.workflow_help"),
            )
        overrides["voice_id"] = None
        overrides["tts_workflow"] = workflow_value.strip() or None
        overrides["ref_audio"] = None
        overrides["tts_provider"] = None
        overrides["tts_model"] = None
        overrides["tts_voice_id"] = None
        overrides["tts_volume"] = None
        overrides["tts_speed"] = st.slider(
            tr("history.edit.tts_speed"),
            min_value=0.5,
            max_value=2.0,
            value=float(getattr(config, "tts_speed", None) or 1.2),
            step=0.1,
            format="%.1fx",
            key=f"{prefix}_comfy_tts_speed",
        )
    else:
        from pixelle_video.config import config_manager

        zh = get_language() == "zh_CN"
        tts_models_config = get_tts_models_config(config_manager)
        minimax_config = (tts_models_config.get("providers", {}) or {}).get("minimax", {})
        provider = getattr(config, "tts_provider", None) or "minimax"
        model = (
            getattr(config, "tts_model", None)
            or minimax_config.get("default_model")
            or tts_models_config.get("default_model")
            or "speech-2.8-turbo"
        )
        current_voice_id = resolve_history_api_tts_voice_id(config, minimax_config)
        voice_input_key = f"{prefix}_api_voice_id_{current_voice_id}"

        st.caption(
            f"MiniMax 模型：{model}"
            if zh
            else f"MiniMax model: {model}"
        )
        voice_id = st.text_input(
            "MiniMax voice_id" if zh else "MiniMax voice_id",
            value=current_voice_id,
            key=voice_input_key,
        ).strip()
        overrides["voice_id"] = None
        overrides["tts_workflow"] = None
        overrides["ref_audio"] = None
        overrides["tts_provider"] = provider
        overrides["tts_model"] = model
        overrides["tts_voice_id"] = voice_id or None
        overrides["tts_speed"] = st.slider(
            tr("history.edit.tts_speed"),
            min_value=0.5,
            max_value=2.0,
            value=float(getattr(config, "tts_speed", None) or 1.0),
            step=0.1,
            format="%.1fx",
            key=f"{prefix}_api_tts_speed",
        )
        overrides["tts_volume"] = st.slider(
            "音量" if zh else "Volume",
            min_value=0.0,
            max_value=2.0,
            value=float(getattr(config, "tts_volume", None) or 1.0),
            step=0.1,
            format="%.1fx",
            key=f"{prefix}_api_tts_volume",
        )

    persist = st.checkbox(
        tr("history.edit.persist_overrides"),
        value=False,
        key=f"{prefix}_persist",
    )
    return overrides, persist


def _render_media_overrides(prefix: str, pixelle_video, config) -> tuple[dict, bool]:
    """Render compact media workflow override controls for one-frame regeneration."""
    current_workflow = getattr(config, "media_workflow", None) or ""
    options, keys = _workflow_options(getattr(pixelle_video, "media", None), current_workflow)
    labels = [tr("history.edit.keep_current_workflow")]
    values = [current_workflow]
    labels.extend(options)
    values.extend(keys)

    default_index = values.index(current_workflow) if current_workflow in values else 0
    selected = st.selectbox(
        tr("history.edit.media_workflow"),
        labels,
        index=default_index,
        key=f"{prefix}_media_workflow_select",
    )
    selected_workflow = values[labels.index(selected)]
    persist = st.checkbox(
        tr("history.edit.persist_media_overrides"),
        value=False,
        key=f"{prefix}_persist_media",
    )
    return {"media_workflow": selected_workflow or None}, persist


def _render_template_replacement(prefix: str, config) -> str | None:
    from pixelle_video.utils.template_util import (
        get_template_type,
        get_templates_grouped_by_size_and_type,
    )

    current_template = getattr(config, "frame_template", None) or ""
    current_type = get_template_type(os.path.basename(current_template))
    grouped_templates = get_templates_grouped_by_size_and_type(current_type)
    template_options = []
    template_values = []
    for size, templates in grouped_templates.items():
        for template in templates:
            value = template.template_path
            template_values.append(value)
            template_options.append(f"{template.display_info.name} ({size})")

    if not template_values:
        st.warning(tr("history.edit.no_templates"))
        return None

    default_index = template_values.index(current_template) if current_template in template_values else 0
    selected = st.selectbox(
        tr("history.edit.frame_template"),
        template_options,
        index=default_index,
        key=f"{prefix}_template_select",
    )
    selected_template = template_values[template_options.index(selected)]
    st.caption(tr("history.edit.template_replace_hint", template_type=current_type))
    return selected_template


def _render_insert_frame_controls(
    task_id: str,
    position: int,
    label: str,
    key_suffix: str,
    pixelle_video,
    storyboard,
    image_source_index: int | None = None,
):
    """Render controls for inserting and immediately generating one new frame."""
    is_child_frame = image_source_index is not None
    with st.expander(label, expanded=False):
        narration = st.text_area(
            tr("history.edit.new_frame_narration"),
            value="",
            height=90,
            key=f"insert_narration_{task_id}_{key_suffix}",
        )
        image_prompt = None
        if is_child_frame:
            st.caption(tr("history.edit.new_child_frame_hint"))
        else:
            image_prompt = st.text_area(
                tr("history.edit.new_frame_image_prompt"),
                value="",
                height=100,
                key=f"insert_prompt_{task_id}_{key_suffix}",
                help=tr("history.edit.new_frame_prompt_help"),
            )
        with st.expander(tr("history.edit.single_audio_options"), expanded=False):
            tts_overrides, persist_tts = _render_tts_overrides(
                f"insert_audio_{task_id}_{key_suffix}",
                pixelle_video,
                storyboard.config,
            )
        media_overrides = None
        persist_media = False
        if not is_child_frame:
            with st.expander(tr("history.edit.single_media_options"), expanded=False):
                media_overrides, persist_media = _render_media_overrides(
                    f"insert_media_{task_id}_{key_suffix}",
                    pixelle_video,
                    storyboard.config,
                )

        if st.button(
            tr("history.edit.insert_frame_submit"),
            key=f"insert_frame_submit_{task_id}_{key_suffix}",
            use_container_width=True,
        ):
            if not narration.strip():
                st.error(tr("history.edit.new_frame_narration_required"))
                return
            with st.spinner(tr("history.edit.processing")):
                insert_action_key = (
                    f"insert_frame:{task_id}:{key_suffix}:"
                    f"{image_source_index}:{narration.strip()}"
                )
                _run_edit_action(
                    lambda: pixelle_video.history.insert_frame(
                        task_id,
                        position,
                        narration,
                        image_prompt,
                        tts_overrides=tts_overrides,
                        media_overrides=media_overrides,
                        persist_overrides=persist_tts or persist_media,
                        image_source_index=image_source_index,
                    ),
                    tr("history.edit.success"),
                    action_key=insert_action_key,
                )


def _child_insert_position(storyboard, parent_index: int) -> int:
    related_indexes = [
        frame.index
        for frame in storyboard.frames
        if frame.index == parent_index or frame.image_source_index == parent_index
    ]
    return max(related_indexes) + 1


def _cascade_delete_count(storyboard, frame_index: int) -> int:
    """Return how many frames would be removed by deleting frame_index."""
    delete_indexes = {frame_index}
    changed = True
    while changed:
        changed = False
        for frame in storyboard.frames:
            if frame.image_source_index in delete_indexes and frame.index not in delete_indexes:
                delete_indexes.add(frame.index)
                changed = True
    return len(delete_indexes)


def _render_delete_frame_controls(task_id: str, pixelle_video, storyboard, frame):
    delete_count = _cascade_delete_count(storyboard, frame.index)
    remaining_count = len(storyboard.frames) - delete_count
    with st.expander(tr("history.edit.delete_frame_tools"), expanded=False):
        if delete_count > 1:
            st.warning(tr("history.edit.delete_frame_cascade_hint", count=delete_count))
        if remaining_count <= 0:
            st.info(tr("history.edit.delete_frame_last_hint"))
            return

        confirmed = st.checkbox(
            tr("history.edit.delete_frame_confirm"),
            value=False,
            key=f"delete_frame_confirm_{task_id}_{frame.index}",
        )
        if st.button(
            tr("history.edit.delete_frame_submit"),
            key=f"delete_frame_submit_{task_id}_{frame.index}",
            use_container_width=True,
            disabled=not confirmed,
        ):
            with st.spinner(tr("history.edit.processing")):
                _run_edit_action(
                    lambda: pixelle_video.history.delete_frame(task_id, frame.index),
                    tr("history.edit.success"),
                    on_success=lambda _result: clear_history_frame_edit_state(
                        st.session_state,
                        task_id,
                    ),
                )


def _render_all_audio_progress_callback(prefix: str):
    progress_bar = st.progress(0.0)
    status = st.empty()

    def _callback(event):
        progress_bar.progress(event.progress)
        if event.frame_current and event.frame_total:
            status.caption(
                tr(
                    "history.edit.progress_frame",
                    current=event.frame_current,
                    total=event.frame_total,
                )
            )
        elif event.extra_info == "completed":
            status.caption(tr("history.edit.progress_completed"))
        else:
            status.caption(tr("history.edit.processing"))

    return _callback


def _is_streamlit_control_exception(error: BaseException) -> bool:
    return error.__class__.__name__ in {"RerunException", "StopException"}


def _is_task_edit_cancelled(error: BaseException) -> bool:
    return error.__class__.__name__ == "TaskEditCancelled"


def _run_edit_action(
    action,
    success_message: str,
    cancel_action=None,
    action_key: str | None = None,
    on_success=None,
):
    """Run a history edit action and refresh the page on success."""
    duplicate_state_key = f"history_edit_completed_at_{action_key}" if action_key else None
    now = datetime.now().timestamp()
    if duplicate_state_key and is_recent_duplicate_action(
        last_completed_at=st.session_state.get(duplicate_state_key),
        now=now,
    ):
        st.warning(tr("history.edit.duplicate_ignored"))
        return

    try:
        result = run_async(action())
        if duplicate_state_key:
            st.session_state[duplicate_state_key] = datetime.now().timestamp()
        if on_success:
            on_success(result)
        st.success(success_message)
        if result and result.get("video_path"):
            st.caption(result["video_path"])
        st.rerun()
    except BaseException as e:  # noqa: BLE001
        if _is_streamlit_control_exception(e):
            if cancel_action:
                try:
                    run_async(cancel_action())
                except Exception as cancel_error:  # noqa: BLE001
                    logger.warning(f"Failed to request edit cancellation: {cancel_error}")
            raise
        if _is_task_edit_cancelled(e):
            st.warning(tr("history.edit.cancelled"))
            return
        if not isinstance(e, Exception):
            raise
        logger.exception(e)
        st.error(tr("history.edit.failed", error=str(e)))


def _ensure_history_edit_capabilities(pixelle_video):
    """Repair cached Streamlit core objects that predate history edit methods."""
    required_methods = (
        "remove_bgm",
        "update_bgm",
        "regenerate_all_audio",
        "regenerate_frame_audio",
        "regenerate_frame_media",
        "insert_frame",
        "delete_frame",
        "replace_template",
        "request_cancel_edit",
    )
    history = getattr(pixelle_video, "history", None)
    if history and all(hasattr(history, method) for method in required_methods):
        return pixelle_video

    try:
        import pixelle_video.services.history_manager as history_manager_module
        import pixelle_video.services.task_editor as task_editor_module

        history_manager_module = importlib.reload(history_manager_module)
        task_editor_module = importlib.reload(task_editor_module)
        HistoryManager = history_manager_module.HistoryManager
        TaskEditService = task_editor_module.TaskEditService

        if getattr(pixelle_video, "persistence", None) is None:
            return pixelle_video

        pixelle_video.task_editor = TaskEditService(pixelle_video)
        pixelle_video.history = HistoryManager(
            pixelle_video.persistence,
            task_editor=pixelle_video.task_editor,
        )
        logger.info("Repaired cached PixelleVideoCore history edit capabilities")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to repair history edit capabilities: {e}")

    return pixelle_video


def render_sidebar_controls(pixelle_video):
    """Render sidebar with statistics and filters"""
    with st.sidebar:
        # Statistics
        st.markdown(f"**📊 {tr('history.total_tasks')}**")
        stats = run_async(pixelle_video.history.get_statistics())
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric(tr("history.completed_count"), stats.get("completed", 0))
        with col2:
            st.metric(tr("history.failed_count"), stats.get("failed", 0))
        
        st.divider()
        
        # Filters
        st.markdown(f"**🔍 {tr('history.filter_status')}**")
        status_options = {
            "all": tr("history.status_all"),
            "completed": tr("history.status_completed"),
            "failed": tr("history.status_failed"),
            "running": tr("history.status_running"),
            "pending": tr("history.status_pending"),
        }
        
        selected_status = st.selectbox(
            tr("history.filter_status"),
            options=list(status_options.keys()),
            format_func=lambda x: status_options[x],
            key="filter_status",
            label_visibility="collapsed"
        )
        
        filter_status = None if selected_status == "all" else selected_status
        
        # Sort
        st.markdown(f"**📊 {tr('history.sort_by')}**")
        
        sort_options = {
            "created_at": tr("history.sort_created_at"),
            "completed_at": tr("history.sort_completed_at"),
            "title": tr("history.sort_title"),
            "duration": tr("history.sort_duration"),
        }
        
        sort_by = st.selectbox(
            tr("history.sort_by"),
            options=list(sort_options.keys()),
            format_func=lambda x: sort_options[x],
            key="sort_by",
            label_visibility="collapsed"
        )
        
        sort_order_options = {
            "desc": tr("history.sort_order_desc"),
            "asc": tr("history.sort_order_asc"),
        }
        
        sort_order = st.radio(
            "Sort Order",
            options=list(sort_order_options.keys()),
            format_func=lambda x: sort_order_options[x],
            key="sort_order",
            label_visibility="collapsed",
            horizontal=True
        )
        
        # Page size
        page_size = st.selectbox(
            tr("history.page_size"),
            options=[15, 30, 60],
            index=0,
            key="page_size"
        )
        
        return filter_status, sort_by, sort_order, page_size


def render_grid_task_card(task: dict, pixelle_video):
    """Render a compact grid task card"""
    task_id = task["task_id"]
    title = task.get("title", "Untitled")
    status = task.get("status", "unknown")
    created_at = task.get("created_at", "")
    duration = task.get("duration", 0)
    n_frames = task.get("n_frames", 0)
    video_path = task.get("video_path", "")
    # Resume payload: error message and the page that originally launched
    # the task. Both are propagated from the task's metadata.json into the
    # index entry by PersistenceService so the card doesn't have to round-
    # trip each task's metadata file on every render.
    error_message = task.get("error") or ""
    source_page = task.get("source_page") or "1_🎬_Home"
    # UI-side pipeline name (e.g. "quick_create" / "custom_media"). Used to
    # write a *per-pipeline* session-state key on Resume so each tab on the
    # Home page only consumes its own resume hint. Falls back to
    # "quick_create" for tasks created before this field existed; old
    # standard-pipeline tasks resume into the standard tab and old asset-
    # based tasks would get a None source_pipeline — for those, see the 🔄
    # click handler below for fallback behavior.
    source_pipeline = task.get("source_pipeline") or "quick_create"

    # Status badge
    status_map = {
        "completed": "✅",
        "failed": "❌",
        "running": "⏳",
        "pending": "⏸️",
    }
    status_icon = status_map.get(status, "❓")

    # Get input text
    detail = run_async(pixelle_video.history.get_task_detail(task_id))
    input_text = ""
    if detail and detail.get("metadata"):
        input_params = detail["metadata"].get("input", {})
        input_text = input_params.get("text", "")

    # Card container
    with st.container():
        # Video preview at top
        if video_path and os.path.exists(video_path):
            _render_local_video(video_path, autoplay=False, loop=False, muted=False)
        else:
            st.markdown(
                "<div style='background: #f0f0f0; height: 180px; display: flex; align-items: center; "
                "justify-content: center; border-radius: 4px; font-size: 48px;'>📹</div>",
                unsafe_allow_html=True
            )

        # Title + Status (compact) - show actual title from task
        st.markdown(f"**{status_icon} {truncate_text(title, 50)}**")

        # Input content (very short)
        if input_text:
            st.caption(truncate_text(input_text, 60))

        # Meta info (one line)
        st.caption(f"🕒 {format_datetime(created_at)} | ⏱️ {format_duration(duration)} | 🎬 {n_frames}")

        # Failure detail line: surface the persisted error so the user knows
        # why this task is recoverable.
        if status == "failed" and error_message:
            st.caption(f"❌ {truncate_text(error_message, 80)}")

        # Action buttons (compact). Failed tasks get a 4th 🔄 Resume button.
        is_failed = status == "failed"
        if is_failed:
            col1, col2, col3, col4 = st.columns(4)
        else:
            col1, col2, col3 = st.columns(3)

        with col1:
            if st.button("👁️", key=f"view_{task_id}", help=tr("history.task_card.view_detail"), use_container_width=True):
                st.session_state[f"detail_{task_id}"] = True
                st.rerun()

        with col2:
            if video_path and os.path.exists(video_path):
                with open(video_path, "rb") as f:
                    st.download_button(
                        "⬇️",
                        data=f,
                        file_name=f"{title}.mp4",
                        mime="video/mp4",
                        key=f"download_{task_id}",
                        help=tr("history.task_card.download"),
                        use_container_width=True
                    )
            else:
                st.button("⬇️", key=f"download_disabled_{task_id}", disabled=True, use_container_width=True)

        with col3:
            if st.button("🗑️", key=f"delete_{task_id}", help=tr("history.task_card.delete"), use_container_width=True):
                st.session_state[f"confirm_delete_{task_id}"] = True
                st.rerun()

        if is_failed:
            with col4:
                # 🔄 Resume: stash the task_id under a *per-pipeline* session
                # key so each tab on the Home page only consumes its own
                # resume hint (the alternative — a shared key — was racy
                # because Streamlit renders all tabs in a single script run
                # and whichever tab pops first wins). Then route the user
                # back to the page that originally launched the task.
                if st.button(
                    "🔄",
                    key=f"resume_{task_id}",
                    help=tr("history.task_card.resume"),
                    use_container_width=True,
                ):
                    st.session_state[f"resume_task_id_{source_pipeline}"] = task_id
                    target = f"pages/{source_page}.py"
                    try:
                        st.switch_page(target)
                    except Exception as e:
                        # Fall back to Home if the recorded source_page is
                        # missing (renamed page, older task without the
                        # source_page tag, etc.).
                        logger.warning(
                            f"switch_page({target}) failed: {e}; "
                            f"falling back to Home"
                        )
                        st.switch_page("pages/1_🎬_Home.py")
        
        # Delete confirmation (show in modal-like way)
        if st.session_state.get(f"confirm_delete_{task_id}", False):
            st.warning("⚠️ 确认删除?")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("✅", key=f"confirm_yes_{task_id}", use_container_width=True):
                    try:
                        success = run_async(pixelle_video.history.delete_task(task_id))
                        if success:
                            st.success(tr("history.action.delete_success"))
                            st.session_state[f"confirm_delete_{task_id}"] = False
                            st.rerun()
                        else:
                            st.error("删除失败")
                    except Exception as e:
                        st.error(f"删除失败: {str(e)}")
            with col2:
                if st.button("❌", key=f"confirm_no_{task_id}", use_container_width=True):
                    st.session_state[f"confirm_delete_{task_id}"] = False
                    st.rerun()


def render_task_detail_modal(task_id: str, pixelle_video):
    """Render task detail in three-column layout"""
    detail = run_async(pixelle_video.history.get_task_detail(task_id))
    
    if not detail:
        st.error("Task not found")
        return
    
    metadata = detail["metadata"]
    storyboard = detail["storyboard"]
    
    # Close button at the top
    if st.button("❌ " + tr("history.detail.close"), key=f"close_detail_top_{task_id}"):
        st.session_state[f"detail_{task_id}"] = False
        st.rerun()
    
    st.markdown(f"**{tr('history.detail.modal_title')}**")
    st.caption(f"{tr('history.detail.task_id')}: {task_id}")
    
    # Three-column layout
    col_input, col_storyboard, col_video = st.columns([1, 1, 1])
    
    # Left column: Input and config
    with col_input:
        st.markdown(f"**📝 {tr('history.detail.input_params')}**")

        input_params = metadata.get("input", {})

        # If this task ended in failure, surface the persisted error
        # message so the user can decide whether to resume or delete.
        if metadata.get("status") == "failed" and metadata.get("error"):
            st.error(f"**{tr('history.detail.error')}**: {metadata['error']}")

        # Display input parameters
        st.markdown(f"**{tr('history.detail.mode')}:** {input_params.get('mode', 'N/A')}")
        st.markdown(f"**{tr('history.detail.n_scenes')}:** {input_params.get('n_scenes', 'N/A')}")
        st.markdown(f"**{tr('history.detail.tts_mode')}:** {input_params.get('tts_inference_mode', 'N/A')}")
        st.markdown(f"**{tr('history.detail.voice')}:** {input_params.get('tts_voice', 'N/A')}")
        
        # Input text
        with st.expander(tr("history.detail.text"), expanded=True):
            st.text_area(
                "Input Text",
                value=input_params.get('text', 'N/A'),
                height=200,
                disabled=True,
                label_visibility="collapsed"
            )
    
    # Middle column: Storyboard frames
    with col_storyboard:
        st.markdown(f"**🎬 {tr('history.detail.storyboard')}**")
        
        if storyboard and storyboard.frames:
            _render_insert_frame_controls(
                task_id,
                0,
                tr("history.edit.insert_frame_at_start"),
                "start",
                pixelle_video,
                storyboard,
            )
            for frame in storyboard.frames:
                with st.expander(f"{tr('history.detail.frame')} {frame.index + 1}", expanded=False):
                    st.markdown(f"**{tr('history.detail.narration')}:**")
                    st.caption(frame.narration)
                    
                    if frame.image_prompt:
                        st.markdown(f"**{tr('history.detail.image_prompt')}:**")
                        st.caption(frame.image_prompt)
                    
                    # Show frame preview (small)
                    col1, col2 = st.columns(2)
                    with col1:
                        if frame.composed_image_path and os.path.exists(frame.composed_image_path):
                            st.image(frame.composed_image_path)
                        elif frame.image_path and os.path.exists(frame.image_path):
                            st.image(frame.image_path)
                    with col2:
                        if frame.video_segment_path and os.path.exists(frame.video_segment_path):
                            _render_local_video(frame.video_segment_path)
                    
                    # Audio player (compact)
                    if frame.audio_path and os.path.exists(frame.audio_path):
                        st.audio(frame.audio_path)

                    st.divider()
                    edited_narration = st.text_area(
                        tr("history.edit.narration_label"),
                        value=frame.narration or "",
                        height=90,
                        key=f"edit_narration_{task_id}_{frame.index}",
                    )
                    with st.expander(tr("history.edit.single_audio_options"), expanded=False):
                        tts_overrides, persist_tts = _render_tts_overrides(
                            f"frame_audio_{task_id}_{frame.index}",
                            pixelle_video,
                            storyboard.config,
                        )
                    if st.button(
                        tr("history.edit.regenerate_frame_audio"),
                        key=f"regenerate_audio_{task_id}_{frame.index}",
                        use_container_width=True,
                    ):
                        with st.spinner(tr("history.edit.processing")):
                            _run_edit_action(
                                lambda: pixelle_video.history.regenerate_frame_audio(
                                    task_id,
                                    frame.index,
                                    edited_narration,
                                    tts_overrides=tts_overrides,
                                    persist_overrides=persist_tts,
                                ),
                                tr("history.edit.success"),
                            )

                    if frame.image_prompt is not None:
                        edited_prompt = st.text_area(
                            tr("history.edit.image_prompt_label"),
                            value=frame.image_prompt or "",
                            height=100,
                            key=f"edit_prompt_{task_id}_{frame.index}",
                        )
                        with st.expander(tr("history.edit.single_media_options"), expanded=False):
                            media_overrides, persist_media = _render_media_overrides(
                                f"frame_media_{task_id}_{frame.index}",
                                pixelle_video,
                                storyboard.config,
                            )
                        if st.button(
                            tr("history.edit.regenerate_frame_media"),
                            key=f"regenerate_media_{task_id}_{frame.index}",
                            use_container_width=True,
                        ):
                            with st.spinner(tr("history.edit.processing")):
                                _run_edit_action(
                                    lambda: pixelle_video.history.regenerate_frame_media(
                                        task_id,
                                        frame.index,
                                        edited_prompt,
                                        media_overrides=media_overrides,
                                        persist_overrides=persist_media,
                                    ),
                                    tr("history.edit.success"),
                                )
                    elif frame.image_source_index is not None:
                        st.caption(tr("history.edit.media_child_hint"))

                    _render_insert_frame_controls(
                        task_id,
                        frame.index + 1,
                        tr("history.edit.insert_frame_after"),
                        f"after_{frame.index}",
                        pixelle_video,
                        storyboard,
                    )
                    if frame.image_source_index is None:
                        _render_insert_frame_controls(
                            task_id,
                            _child_insert_position(storyboard, frame.index),
                            tr("history.edit.insert_child_frame"),
                            f"child_of_{frame.index}",
                            pixelle_video,
                            storyboard,
                            image_source_index=frame.index,
                        )
                    _render_delete_frame_controls(task_id, pixelle_video, storyboard, frame)
        else:
            st.info("No storyboard data")
    
    # Right column: Final video
    with col_video:
        st.markdown(f"**🎥 {tr('info.video_information')}**")
        
        video_path = metadata.get("result", {}).get("video_path")
        if video_path and os.path.exists(video_path):
            _render_local_video(video_path)
            
            # Video info
            result = metadata.get("result", {})
            st.markdown(f"**{tr('info.duration')}:** {format_duration(result.get('duration', 0))}")
            st.markdown(f"**{tr('info.frames')}:** {result.get('n_frames', 0)}")
            st.markdown(f"**{tr('info.file_size')}:** {format_file_size(result.get('file_size', 0))}")

            with st.expander(tr("history.edit.final_video_tools"), expanded=False):
                if storyboard:
                    selected_template = _render_template_replacement(
                        f"replace_template_{task_id}",
                        storyboard.config,
                    )
                    if selected_template and st.button(
                        tr("history.edit.replace_template"),
                        key=f"replace_template_button_{task_id}",
                        use_container_width=True,
                    ):
                        with st.spinner(tr("history.edit.processing")):
                            _run_edit_action(
                                lambda: pixelle_video.history.replace_template(
                                    task_id,
                                    selected_template,
                                ),
                                tr("history.edit.success"),
                            )

                if st.button(
                    tr("history.edit.remove_bgm"),
                    key=f"remove_bgm_{task_id}",
                    use_container_width=True,
                ):
                    with st.spinner(tr("history.edit.processing")):
                        _run_edit_action(
                            lambda: pixelle_video.history.remove_bgm(task_id),
                            tr("history.edit.success"),
                        )

                bgm_path = st.text_input(
                    tr("history.edit.bgm_path"),
                    value=metadata.get("input", {}).get("bgm_path") or "",
                    key=f"edit_bgm_path_{task_id}",
                )
                bgm_volume = st.slider(
                    tr("history.edit.bgm_volume"),
                    min_value=0.0,
                    max_value=1.0,
                    value=float(metadata.get("input", {}).get("bgm_volume") or 0.2),
                    step=0.05,
                    key=f"edit_bgm_volume_{task_id}",
                )
                bgm_mode = st.selectbox(
                    tr("history.edit.bgm_mode"),
                    ["loop", "once"],
                    index=0 if metadata.get("input", {}).get("bgm_mode", "loop") == "loop" else 1,
                    key=f"edit_bgm_mode_{task_id}",
                )
                if st.button(
                    tr("history.edit.apply_bgm"),
                    key=f"apply_bgm_{task_id}",
                    use_container_width=True,
                ):
                    with st.spinner(tr("history.edit.processing")):
                        _run_edit_action(
                            lambda: pixelle_video.history.update_bgm(
                                task_id,
                                bgm_path.strip() or None,
                                bgm_volume=bgm_volume,
                                bgm_mode=bgm_mode,
                            ),
                            tr("history.edit.success"),
                        )

            if storyboard:
                with st.expander(tr("history.edit.all_audio_tools"), expanded=False):
                    all_tts_overrides, _persist_unused = _render_tts_overrides(
                        f"all_audio_{task_id}",
                        pixelle_video,
                        storyboard.config,
                    )
                    if st.button(
                        tr("history.edit.request_cancel"),
                        key=f"request_cancel_edit_{task_id}",
                        use_container_width=True,
                    ):
                        run_async(pixelle_video.history.request_cancel_edit(task_id))
                        st.warning(tr("history.edit.cancel_requested"))
                    if st.button(
                        tr("history.edit.regenerate_all_audio"),
                        key=f"regenerate_all_audio_{task_id}",
                        use_container_width=True,
                    ):
                        with st.spinner(tr("history.edit.processing")):
                            progress_callback = _render_all_audio_progress_callback(
                                f"all_audio_progress_{task_id}"
                            )
                            _run_edit_action(
                                lambda: pixelle_video.history.regenerate_all_audio(
                                    task_id,
                                    all_tts_overrides,
                                    progress_callback=progress_callback,
                                ),
                                tr("history.edit.success"),
                                cancel_action=lambda: pixelle_video.history.request_cancel_edit(task_id),
                            )

            # Download button
            with open(video_path, "rb") as f:
                # Get title from input (which now includes the generated title)
                title = metadata.get("input", {}).get("title", "video")
                if not title:
                    title = "video"
                st.download_button(
                    tr("history.detail.download_video"),
                    data=f,
                    file_name=f"{title}.mp4",
                    mime="video/mp4",
                    use_container_width=True
                )
        else:
            st.warning("Video file not found")
    
    st.divider()
    
    # Close button at the bottom
    if st.button("❌ " + tr("history.detail.close"), key=f"close_detail_bottom_{task_id}"):
        st.session_state[f"detail_{task_id}"] = False
        st.rerun()


def main():
    """Main entry point for History page"""
    # Initialize
    init_session_state()
    init_i18n()
    
    # Render header
    render_header()
    
    # Initialize Pixelle-Video
    pixelle_video = get_pixelle_video()
    pixelle_video = _ensure_history_edit_capabilities(pixelle_video)
    
    # Sidebar: Statistics + Filters
    filter_status, sort_by, sort_order, page_size = render_sidebar_controls(pixelle_video)
    
    # Initialize pagination in session state
    if "history_page" not in st.session_state:
        st.session_state.history_page = 1
    
    # Check if we need to show a detail view
    show_detail_for = None
    for key in st.session_state.keys():
        if key.startswith("detail_") and st.session_state[key]:
            show_detail_for = key.replace("detail_", "")
            break
    
    # If showing detail, render it
    if show_detail_for:
        render_task_detail_modal(show_detail_for, pixelle_video)
        return
    
    # Otherwise, show the grid list
    # Get task list
    result = run_async(pixelle_video.history.get_task_list(
        page=st.session_state.history_page,
        page_size=page_size,
        status=filter_status,
        sort_by=sort_by,
        sort_order=sort_order
    ))
    
    tasks = result["tasks"]
    total = result["total"]
    total_pages = result["total_pages"]
    
    # Page title with count
    st.markdown(f"##### 📚 {tr('history.page_title')} ({total})")
    
    # Show task cards in grid layout (4 columns)
    if not tasks:
        st.info(tr("history.no_tasks"))
    else:
        # Grid layout: 4 cards per row
        CARDS_PER_ROW = 4
        
        # Process tasks in batches of CARDS_PER_ROW
        for i in range(0, len(tasks), CARDS_PER_ROW):
            cols = st.columns(CARDS_PER_ROW)
            
            # Fill each column with a task card
            for j in range(CARDS_PER_ROW):
                task_idx = i + j
                if task_idx < len(tasks):
                    with cols[j]:
                        render_grid_task_card(tasks[task_idx], pixelle_video)
    
    # Pagination
    if total_pages > 1:
        st.divider()
        col1, col2, col3 = st.columns([1, 2, 1])
        
        with col1:
            if st.button("⬅️ Previous", disabled=st.session_state.history_page == 1, use_container_width=True):
                st.session_state.history_page -= 1
                st.rerun()
        
        with col2:
            st.markdown(
                f"<div style='text-align: center; padding-top: 8px;'>"
                f"{tr('history.page_info').format(page=st.session_state.history_page, total_pages=total_pages)}"
                f"</div>",
                unsafe_allow_html=True
            )
        
        with col3:
            if st.button("Next ➡️", disabled=st.session_state.history_page == total_pages, use_container_width=True):
                st.session_state.history_page += 1
                st.rerun()


if __name__ == "__main__":
    main()
