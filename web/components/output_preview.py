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
Output preview components for web UI (right column)
"""

import os

import streamlit as st
from loguru import logger

from pixelle_video.config import config_manager
from pixelle_video.models.progress import ProgressEvent
from web.i18n import get_language, tr
from web.utils.async_helpers import run_async


def render_output_preview(pixelle_video, video_params):
    """Render output preview section (right column)"""
    # Check if batch mode
    is_batch = video_params.get("batch_mode", False)
    
    if is_batch:
        # Batch generation mode
        render_batch_output(pixelle_video, video_params)
    else:
        # Single video generation mode (original logic)
        render_single_output(pixelle_video, video_params)


def render_single_output(pixelle_video, video_params):
    """Render single video generation output (original logic, unchanged)"""
    # Resume support: each Home-page tab has its OWN session-state key so it
    # can't accidentally consume another tab's resume hint. The Standard
    # pipeline UI registers itself as ``quick_create`` (see
    # ``web/pipelines/standard.py``); that's the suffix the History page
    # writes when the user clicks 🔄 on a failed standard-pipeline task.
    pipeline_name = video_params.get("pipeline", "quick_create")
    resume_state_key = f"resume_task_id_{pipeline_name}"
    resume_task_id = st.session_state.pop(resume_state_key, None)
    if resume_task_id:
        st.info(tr("history.resume_banner", task_id=resume_task_id))

    # Extract parameters from video_params dict
    text = video_params.get("text", "")
    mode = video_params.get("mode", "generate")
    title = video_params.get("title")
    n_scenes = video_params.get("n_scenes", 5)
    split_mode = video_params.get("split_mode", "paragraph")
    content_style = video_params.get("content_style", "general")
    min_narration_words = video_params.get("min_narration_words", 5)
    max_narration_words = video_params.get("max_narration_words", 20)
    bgm_path = video_params.get("bgm_path")
    bgm_volume = video_params.get("bgm_volume", 0.2)
    
    tts_mode = video_params.get("tts_inference_mode", "local")
    selected_voice = video_params.get("tts_voice")
    tts_speed = video_params.get("tts_speed")
    tts_volume = video_params.get("tts_volume")
    tts_provider = video_params.get("tts_provider")
    tts_model = video_params.get("tts_model")
    tts_voice_id = video_params.get("tts_voice_id")
    tts_workflow_key = video_params.get("tts_workflow")
    ref_audio_path = video_params.get("ref_audio")
    
    frame_template = video_params.get("frame_template")
    custom_values_for_video = video_params.get("template_params", {})
    workflow_key = video_params.get("media_workflow")
    api_video_params = video_params.get("api_video_params")
    prompt_prefix = video_params.get("prompt_prefix", "")

    approved_script = None
    approved_title = title

    if mode == "generate":
        approved_script, approved_title = _render_script_preview_section(
            pixelle_video=pixelle_video,
            text=text,
            title=title,
            n_scenes=n_scenes,
            content_style=content_style,
            min_narration_words=min_narration_words,
            max_narration_words=max_narration_words,
        )
    
    with st.container(border=True):
        st.markdown(f"**{tr('section.video_generation')}**")
        
        # Check if system is configured
        if not config_manager.validate():
            st.warning(tr("settings.not_configured"))
        
        # Generate Button
        if st.button(tr("btn.generate"), type="primary", use_container_width=True):
            # Validate system configuration
            if not config_manager.validate():
                st.error(tr("settings.not_configured"))
                st.stop()
            
            # Validate input
            if mode == "generate" and approved_script:
                effective_text = approved_script
                effective_mode = "fixed"
                effective_title = approved_title or title
                effective_split_mode = "paragraph"
            else:
                if not text:
                    st.error(tr("error.input_required"))
                    st.stop()
                effective_text = text
                effective_mode = mode
                effective_title = title
                effective_split_mode = split_mode

            from pixelle_video.utils.template_util import get_template_type
            if frame_template and get_template_type(frame_template) == "video" and not workflow_key:
                st.error(
                    "请选择 API 视频生成模型后再生成。"
                    if get_language() == "zh_CN"
                    else "Please select an API video generation model before generating."
                )
                st.stop()
            
            # Show progress
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            # Record start time for generation
            import time
            start_time = time.time()
            
            try:
                # Progress callback to update UI
                def update_progress(event: ProgressEvent):
                    """Update progress bar and status text from ProgressEvent"""
                    # Translate event to user-facing message
                    if event.event_type == "frame_step":
                        # Frame step: "分镜 3/5 - 步骤 2/4: 生成插图"
                        action_key = f"progress.step_{event.action}"
                        action_text = tr(action_key)
                        message = tr(
                            "progress.frame_step",
                            current=event.frame_current,
                            total=event.frame_total,
                            step=event.step,
                            action=action_text
                        )
                    elif event.event_type == "processing_frame":
                        # Processing frame: "分镜 3/5"
                        message = tr(
                            "progress.frame",
                            current=event.frame_current,
                            total=event.frame_total
                        )
                    else:
                        # Simple events: use i18n key directly
                        message = tr(f"progress.{event.event_type}")
                    
                    # Append extra_info if available (e.g., batch progress)
                    if event.extra_info:
                        message = f"{message} - {event.extra_info}"
                    
                    status_text.text(message)
                    progress_bar.progress(min(int(event.progress * 100), 99))  # Cap at 99% until complete
                
                # Generate video (directly pass parameters)
                # Note: media_width and media_height are auto-determined from template
                gen_params = {
                    "text": effective_text,
                    "mode": effective_mode,
                    "title": effective_title if effective_title else None,
                    "n_scenes": n_scenes,
                    "split_mode": effective_split_mode,
                    "content_style": content_style,
                    "min_narration_words": min_narration_words,
                    "max_narration_words": max_narration_words,
                    "media_workflow": workflow_key,
                    "api_video_params": api_video_params,
                    "frame_template": frame_template,
                    "prompt_prefix": prompt_prefix,
                    "bgm_path": bgm_path,
                    "bgm_volume": bgm_volume if bgm_path else 0.2,
                    "progress_callback": update_progress,
                    "media_width": st.session_state.get('template_media_width'),
                    "media_height": st.session_state.get('template_media_height'),
                    # Stamp the source page so the History card's Resume
                    # button can route back here. All Standard/Asset-based
                    # pipelines live under the Home page tabs.
                    "source_page": "1_🎬_Home",
                    # UI-side pipeline name; the History page uses this to
                    # write a per-pipeline resume key when 🔄 is clicked.
                    "source_pipeline": pipeline_name,
                }
                # Pipe resume hint through to the core pipeline.
                if resume_task_id:
                    gen_params["resume_task_id"] = resume_task_id
                # Add TTS parameters based on mode
                gen_params["tts_inference_mode"] = tts_mode
                if tts_mode == "local":
                    gen_params["tts_voice"] = selected_voice
                    gen_params["tts_speed"] = tts_speed
                elif tts_mode == "comfyui":
                    gen_params["tts_workflow"] = tts_workflow_key
                    if ref_audio_path:
                        gen_params["ref_audio"] = str(ref_audio_path)
                elif tts_mode == "api":
                    if not tts_voice_id:
                        st.error(
                            "请先拉取并选择 MiniMax 音色。"
                            if get_language() == "zh_CN"
                            else "Please fetch and select a MiniMax voice first."
                        )
                        st.stop()
                    gen_params["tts_provider"] = tts_provider
                    gen_params["tts_model"] = tts_model
                    gen_params["tts_voice_id"] = tts_voice_id
                    gen_params["tts_speed"] = tts_speed
                    gen_params["tts_volume"] = tts_volume
                
                # Add custom template parameters if any
                if custom_values_for_video:
                    gen_params["template_params"] = custom_values_for_video
                
                result = run_async(pixelle_video.generate_video(**gen_params))
                
                # Calculate total generation time
                total_generation_time = time.time() - start_time
                
                progress_bar.progress(100)
                status_text.text(tr("status.success"))
                
                # Display success message
                st.success(tr("status.video_generated", path=result.video_path))
                
                st.markdown("---")
                
                # Video information (compact display)
                file_size_mb = result.file_size / (1024 * 1024)
                
                # Parse video size from template path
                from pixelle_video.utils.template_util import (
                    parse_template_size,
                    resolve_template_path,
                )
                template_path = resolve_template_path(result.storyboard.config.frame_template)
                video_width, video_height = parse_template_size(template_path)
                
                info_text = (
                    f"⏱️ {tr('info.generation_time')} {total_generation_time:.1f}s   "
                    f"📦 {file_size_mb:.2f}MB   "
                    f"🎬 {len(result.storyboard.frames)}{tr('info.scenes_unit')}   "
                    f"📐 {video_width}x{video_height}"
                )
                st.caption(info_text)
                
                st.markdown("---")
                
                # Video preview
                if os.path.exists(result.video_path):
                    st.video(result.video_path)
                    
                    # Download button
                    with open(result.video_path, "rb") as video_file:
                        video_bytes = video_file.read()
                        video_filename = os.path.basename(result.video_path)
                        st.download_button(
                            label="⬇️ 下载视频" if get_language() == "zh_CN" else "⬇️ Download Video",
                            data=video_bytes,
                            file_name=video_filename,
                            mime="video/mp4",
                            use_container_width=True
                        )
                else:
                    st.error(tr("status.video_not_found", path=result.video_path))
                
            except Exception as e:
                status_text.text("")
                progress_bar.empty()
                st.error(tr("status.error", error=str(e)))
                logger.exception(e)
                st.stop()


def _render_script_preview_section(
    pixelle_video,
    text: str,
    title: str | None,
    n_scenes: int,
    content_style: str,
    min_narration_words: int,
    max_narration_words: int,
):
    """
    Render script preview/edit UI for generate mode.

    Returns:
        (approved_script, approved_title) — script text and title to use for video generation
    """
    with st.container(border=True):
        st.markdown(f"**{tr('section.script_preview')}**")
        st.caption(tr("script_preview.hint"))

        if st.button(tr("btn.generate_script"), use_container_width=True, key="btn_generate_script"):
            if not config_manager.validate():
                st.error(tr("settings.not_configured"))
                st.stop()
            if not text:
                st.error(tr("error.input_required"))
                st.stop()

            with st.spinner(tr("progress.generating_narrations")):
                from pixelle_video.utils.content_generators import generate_script_preview

                try:
                    preview = run_async(generate_script_preview(
                        llm_service=pixelle_video.llm,
                        topic=text,
                        n_scenes=n_scenes,
                        min_words=min_narration_words,
                        max_words=max_narration_words,
                        content_style=content_style,
                        title=title or None,
                    ))
                    st.session_state["script_preview_text"] = "\n\n".join(preview["narrations"])
                    st.session_state["script_preview_title"] = preview["title"]
                    st.success(tr("script_preview.generated"))
                except Exception as e:
                    st.error(tr("status.error", error=str(e)))
                    logger.exception(e)
                    st.stop()

        default_script = st.session_state.get("script_preview_text", "")
        default_preview_title = st.session_state.get("script_preview_title", title or "")

        if default_script:
            edited_script = st.text_area(
                tr("script_preview.edit_label"),
                value=default_script,
                height=220,
                help=tr("script_preview.edit_help"),
                key="script_preview_textarea",
            )
            edited_title = st.text_input(
                tr("script_preview.title_label"),
                value=default_preview_title,
                help=tr("input.title_help"),
            )
            st.session_state["script_preview_text"] = edited_script
            st.session_state["script_preview_title"] = edited_title
            return edited_script.strip() or None, edited_title.strip() or None

    return None, title


def render_batch_output(pixelle_video, video_params):
    """Render batch generation output (minimal, redirect to History)"""
    topics = video_params.get("topics", [])
    
    with st.container(border=True):
        st.markdown(f"**{tr('batch.section_generation')}**")
        
        # Check if topics are provided
        if not topics:
            st.warning(tr("batch.no_topics"))
            return
        
        # Check system configuration
        if not config_manager.validate():
            st.warning(tr("settings.not_configured"))
            return
        
        batch_count = len(topics)
        
        # Display batch info
        st.info(tr("batch.prepare_info", count=batch_count))
        
        # Estimated time (optional)
        estimated_minutes = batch_count * 3  # Assume 3 minutes per video
        st.caption(tr("batch.estimated_time", minutes=estimated_minutes))
        
        # Generate button with batch semantics
        if st.button(
            tr("batch.generate_button", count=batch_count),
            type="primary",
            use_container_width=True,
            help=tr("batch.generate_help")
        ):
            # Prepare shared config
            shared_config = {
                "title_prefix": video_params.get("title_prefix"),
                "n_scenes": video_params.get("n_scenes") or 5,
                "content_style": video_params.get("content_style", "general"),
                "min_narration_words": video_params.get("min_narration_words", 5),
                "max_narration_words": video_params.get("max_narration_words", 20),
                "media_workflow": video_params.get("media_workflow"),
                "api_video_params": video_params.get("api_video_params"),
                "frame_template": video_params.get("frame_template"),
                "prompt_prefix": video_params.get("prompt_prefix") or "",
                "bgm_path": video_params.get("bgm_path"),
                "bgm_volume": video_params.get("bgm_volume") or 0.2,
                "tts_inference_mode": video_params.get("tts_inference_mode") or "local",
                "media_width": video_params.get("media_width"),
                "media_height": video_params.get("media_height"),
            }
            # Add TTS parameters based on mode (only add non-None values)
            if shared_config["tts_inference_mode"] == "local":
                tts_voice = video_params.get("tts_voice")
                tts_speed = video_params.get("tts_speed")
                if tts_voice:
                    shared_config["tts_voice"] = tts_voice
                if tts_speed:
                    shared_config["tts_speed"] = tts_speed
            else:  # comfyui
                tts_workflow = video_params.get("tts_workflow")
                if tts_workflow:
                    shared_config["tts_workflow"] = tts_workflow
                ref_audio = video_params.get("ref_audio")
                if ref_audio:
                    shared_config["ref_audio"] = str(ref_audio)
            
            # Add template parameters
            if video_params.get("template_params"):
                shared_config["template_params"] = video_params["template_params"]
            
            # UI containers
            overall_progress_container = st.container()
            current_task_container = st.container()
            
            # Overall progress UI
            overall_progress_bar = overall_progress_container.progress(0)
            overall_status = overall_progress_container.empty()
            
            # Current task progress UI
            current_task_title = current_task_container.empty()
            current_task_progress = current_task_container.progress(0)
            current_task_status = current_task_container.empty()
            
            # Overall progress callback
            def update_overall_progress(current, total, topic):
                progress = (current - 1) / total
                overall_progress_bar.progress(progress)
                overall_status.markdown(
                    f"📊 **{tr('batch.overall_progress')}**: {current}/{total} ({int(progress * 100)}%)"
                )
            
            # Single task progress callback factory
            def make_task_progress_callback(task_idx, topic):
                def callback(event: ProgressEvent):
                    # Display current task title
                    current_task_title.markdown(f"🎬 **{tr('batch.current_task')} {task_idx}**: {topic}")
                    
                    # Update task detailed progress
                    if event.event_type == "frame_step":
                        action_key = f"progress.step_{event.action}"
                        action_text = tr(action_key)
                        message = tr(
                            "progress.frame_step",
                            current=event.frame_current,
                            total=event.frame_total,
                            step=event.step,
                            action=action_text
                        )
                    elif event.event_type == "processing_frame":
                        message = tr(
                            "progress.frame",
                            current=event.frame_current,
                            total=event.frame_total
                        )
                    else:
                        message = tr(f"progress.{event.event_type}")
                    
                    current_task_progress.progress(event.progress)
                    current_task_status.text(message)
                
                return callback
            
            # Execute batch generation
            import time

            from web.utils.batch_manager import SimpleBatchManager
            
            batch_manager = SimpleBatchManager()
            start_time = time.time()
            
            batch_result = batch_manager.execute_batch(
                pixelle_video=pixelle_video,
                topics=topics,
                shared_config=shared_config,
                overall_progress_callback=update_overall_progress,
                task_progress_callback_factory=make_task_progress_callback
            )
            
            total_time = time.time() - start_time
            
            # Clear progress displays
            overall_progress_bar.progress(1.0)
            overall_status.markdown(f"✅ **{tr('batch.completed')}**")
            current_task_title.empty()
            current_task_progress.empty()
            current_task_status.empty()
            
            # Display results summary
            st.markdown("---")
            st.markdown(f"**{tr('batch.results_title')}**")
            
            col1, col2, col3 = st.columns(3)
            col1.metric(tr("batch.total"), batch_result["total_count"])
            col2.metric(f"✅ {tr('batch.success')}", batch_result["success_count"])
            col3.metric(f"❌ {tr('batch.failed')}", batch_result["failed_count"])
            
            # Display total time
            minutes = int(total_time / 60)
            seconds = int(total_time % 60)
            st.caption(f"⏱️ {tr('batch.total_time')}: {minutes}{tr('batch.minutes')}{seconds}{tr('batch.seconds')}")
            
            # Redirect to History page
            st.markdown("---")
            st.success(tr("batch.success_message"))
            st.info(tr("batch.view_in_history"))
            
            # Button to go to History page using JavaScript URL navigation
            st.markdown(
                f"""
                <a href="/History" target="_blank">
                    <button style="
                        width: 100%;
                        padding: 0.5rem 1rem;
                        background-color: white;
                        color: rgb(49, 51, 63);
                        border: 1px solid rgba(49, 51, 63, 0.2);
                        border-radius: 0.5rem;
                        cursor: pointer;
                        font-size: 1rem;
                        font-weight: 400;
                        text-align: center;
                    ">
                        📚 {tr('batch.goto_history')}
                    </button>
                </a>
                """,
                unsafe_allow_html=True
            )
            
            # Show failed tasks if any
            if batch_result["errors"]:
                st.markdown("---")
                st.markdown(f"#### {tr('batch.failed_list')}")
                
                for item in batch_result["errors"]:
                    with st.expander(f"🔴 {tr('batch.task')} {item['index']}: {item['topic']}", expanded=False):
                        st.error(f"**{tr('batch.error')}**: {item['error']}")
                        
                        # Detailed error (collapsed)
                        with st.expander(tr("batch.error_detail")):
                            st.code(item['traceback'], language="python")
    
