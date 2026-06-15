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
Content generation utility functions

Pure/stateless functions for generating content using LLM.
These functions are reusable across different pipelines.
"""

import json
import re
from typing import List, Optional, Literal

from loguru import logger

ContentStyle = Literal["general", "tech_pop"]

# Default narration parameters per content style
CONTENT_STYLE_DEFAULTS = {
    "general": {"min_words": 5, "max_words": 20, "n_scenes": 5, "temperature": 0.8},
    "tech_pop": {"min_words": 25, "max_words": 55, "n_scenes": 8, "temperature": 0.55},
}

TECH_POP_TITLE_MAX_LENGTH = 22
TECH_POP_IMAGE_STYLE_PRESET = "tech_diagram"


def resolve_content_style_params(
    content_style: ContentStyle = "general",
    n_scenes: Optional[int] = None,
    min_words: Optional[int] = None,
    max_words: Optional[int] = None,
) -> dict:
    """Resolve effective narration parameters for a content style."""
    defaults = CONTENT_STYLE_DEFAULTS[content_style]
    return {
        "n_scenes": n_scenes if n_scenes is not None else defaults["n_scenes"],
        "min_words": min_words if min_words is not None else defaults["min_words"],
        "max_words": max_words if max_words is not None else defaults["max_words"],
        "temperature": defaults["temperature"],
    }


async def generate_title(
    llm_service,
    content: str,
    strategy: Literal["auto", "direct", "llm"] = "auto",
    max_length: int = 15,
    content_style: ContentStyle = "general",
) -> str:
    """
    Generate title from content
    
    Args:
        llm_service: LLM service instance
        content: Source content (topic or script)
        strategy: Generation strategy
            - "auto": Auto-decide based on content length (default)
            - "direct": Use content directly (truncated if needed)
            - "llm": Always use LLM to generate title
        max_length: Maximum title length (default: 15)
    
    Returns:
        Generated title
    """
    if strategy == "direct":
        content = content.strip()
        return content[:max_length] if len(content) > max_length else content
    
    if strategy == "auto":
        if len(content.strip()) <= 15:
            return content.strip()
        # Fall through to LLM
    
    # Use LLM to generate title
    if content_style == "tech_pop":
        from pixelle_video.prompts.tech_pop_title_generation import build_tech_pop_title_prompt
        effective_max_length = max(max_length, TECH_POP_TITLE_MAX_LENGTH)
        prompt = build_tech_pop_title_prompt(content, max_length=effective_max_length)
        temperature = 0.5
    else:
        from pixelle_video.prompts import build_title_generation_prompt
        effective_max_length = max_length
        prompt = build_title_generation_prompt(content, max_length=effective_max_length)
        temperature = 0.7

    response = await llm_service(prompt, temperature=temperature, max_tokens=50)
    
    # Clean up response
    title = response.strip()
    
    # Remove quotes if present
    if title.startswith('"') and title.endswith('"'):
        title = title[1:-1]
    if title.startswith("'") and title.endswith("'"):
        title = title[1:-1]
    
    # Remove trailing punctuation
    title = title.rstrip('.,!?;:\'"')
    
    # Safety: if still over limit, truncate smartly
    if len(title) > effective_max_length:
        # Try to truncate at word boundary
        truncated = title[:effective_max_length]
        last_space = truncated.rfind(' ')
        
        # Only use word boundary if it's not too far back (at least 60% of max_length)
        if last_space > effective_max_length * 0.6:
            title = truncated[:last_space]
        else:
            title = truncated
        
        # Remove any trailing punctuation after truncation
        title = title.rstrip('.,!?;:\'"')
    
    logger.debug(f"Generated title: '{title}' (length: {len(title)})")
    return title


async def generate_narrations_from_topic(
    llm_service,
    topic: str,
    n_scenes: int = 5,
    min_words: int = 5,
    max_words: int = 20,
    content_style: ContentStyle = "general",
    use_two_pass: bool = False,
) -> List[str]:
    """
    Generate narrations from topic using LLM
    
    Args:
        llm_service: LLM service instance
        topic: Topic/theme to generate narrations from
        n_scenes: Number of narrations to generate
        min_words: Minimum narration length
        max_words: Maximum narration length
    
    Returns:
        List of narration texts
    """
    logger.info(f"Generating {n_scenes} narrations from topic: {topic} (style={content_style})")

    if content_style == "tech_pop" and use_two_pass:
        return await generate_tech_script_two_pass(
            llm_service=llm_service,
            topic=topic,
            n_scenes=n_scenes,
            min_words=min_words,
            max_words=max_words,
        )

    if content_style == "tech_pop":
        from pixelle_video.prompts.tech_popularization_narration import build_tech_pop_narration_prompt
        prompt = build_tech_pop_narration_prompt(
            topic=topic,
            n_storyboard=n_scenes,
            min_words=min_words,
            max_words=max_words,
        )
        temperature = CONTENT_STYLE_DEFAULTS["tech_pop"]["temperature"]
    else:
        from pixelle_video.prompts import build_topic_narration_prompt
        prompt = build_topic_narration_prompt(
            topic=topic,
            n_storyboard=n_scenes,
            min_words=min_words,
            max_words=max_words,
        )
        temperature = CONTENT_STYLE_DEFAULTS["general"]["temperature"]
    
    response = await llm_service(
        prompt=prompt,
        temperature=temperature,
        max_tokens=2000
    )
    
    logger.debug(f"LLM response: {response[:200]}...")
    
    # Parse JSON
    result = _parse_json(response)
    
    narrations = _validate_narration_count(result, n_scenes)
    
    logger.info(f"Generated {len(narrations)} narrations successfully")
    return narrations


async def generate_tech_script_two_pass(
    llm_service,
    topic: str,
    n_scenes: int = 8,
    min_words: int = 25,
    max_words: int = 55,
    enable_review: bool = True,
) -> List[str]:
    """
    Three-pass tech popularization script generation:
    1. Outline (structure planning)
    2. Narrations from outline
    3. Optional review/rewrite pass
    """
    from pixelle_video.prompts.tech_popularization_narration import (
        build_tech_pop_outline_prompt,
        build_tech_pop_narrations_from_outline_prompt,
        build_tech_pop_review_prompt,
    )

    temperature = CONTENT_STYLE_DEFAULTS["tech_pop"]["temperature"]
    logger.info(f"Tech two-pass generation: topic={topic[:50]}..., scenes={n_scenes}")

    outline_prompt = build_tech_pop_outline_prompt(topic=topic, n_storyboard=n_scenes)
    outline_response = await llm_service(
        prompt=outline_prompt,
        temperature=temperature,
        max_tokens=2000,
    )
    outline_result = _parse_json(outline_response)
    scenes = outline_result.get("scenes", [])
    if len(scenes) < n_scenes:
        logger.warning(f"Outline has {len(scenes)} scenes, expected {n_scenes}")
    if not scenes:
        raise ValueError("Invalid outline response: missing 'scenes'")

    narrations_prompt = build_tech_pop_narrations_from_outline_prompt(
        topic=topic,
        outline=scenes[:n_scenes],
        n_storyboard=n_scenes,
        min_words=min_words,
        max_words=max_words,
    )
    narrations_response = await llm_service(
        prompt=narrations_prompt,
        temperature=temperature,
        max_tokens=3000,
    )
    narrations_result = _parse_json(narrations_response)
    narrations = _validate_narration_count(narrations_result, n_scenes)

    if enable_review:
        review_prompt = build_tech_pop_review_prompt(
            topic=topic,
            narrations=narrations,
            n_storyboard=n_scenes,
            min_words=min_words,
            max_words=max_words,
        )
        review_response = await llm_service(
            prompt=review_prompt,
            temperature=0.4,
            max_tokens=3000,
        )
        review_result = _parse_json(review_response)
        narrations = _validate_narration_count(review_result, n_scenes)

    logger.info(f"Tech two-pass generation completed: {len(narrations)} narrations")
    return narrations


async def generate_script_preview(
    llm_service,
    topic: str,
    n_scenes: int = 5,
    min_words: int = 5,
    max_words: int = 20,
    content_style: ContentStyle = "general",
    title: Optional[str] = None,
) -> dict:
    """
    Generate narrations and title for preview/edit before video generation.

    Returns:
        {"narrations": [...], "title": "..."}
    """
    narrations = await generate_narrations_from_topic(
        llm_service=llm_service,
        topic=topic,
        n_scenes=n_scenes,
        min_words=min_words,
        max_words=max_words,
        content_style=content_style,
    )

    if title:
        generated_title = title
    else:
        title_content = topic if content_style == "general" else "\n".join(narrations)
        generated_title = await generate_title(
            llm_service=llm_service,
            content=title_content,
            strategy="llm" if content_style == "tech_pop" else "auto",
            content_style=content_style,
        )

    return {"narrations": narrations, "title": generated_title}


def _validate_narration_count(result: dict, n_scenes: int) -> List[str]:
    """Validate and normalize narration count from LLM JSON response."""
    if "narrations" not in result:
        raise ValueError("Invalid response format: missing 'narrations' key")

    narrations = result["narrations"]
    if len(narrations) > n_scenes:
        logger.warning(f"Got {len(narrations)} narrations, taking first {n_scenes}")
        narrations = narrations[:n_scenes]
    elif len(narrations) < n_scenes:
        raise ValueError(f"Expected {n_scenes} narrations, got only {len(narrations)}")
    return narrations


async def generate_narrations_from_content(
    llm_service,
    content: str,
    n_scenes: int = 5,
    min_words: int = 5,
    max_words: int = 20
) -> List[str]:
    """
    Generate narrations from user-provided content using LLM
    
    Args:
        llm_service: LLM service instance
        content: User-provided content
        n_scenes: Number of narrations to generate
        min_words: Minimum narration length
        max_words: Maximum narration length
    
    Returns:
        List of narration texts
    """
    from pixelle_video.prompts import build_content_narration_prompt
    
    logger.info(f"Generating {n_scenes} narrations from content ({len(content)} chars)")
    
    prompt = build_content_narration_prompt(
        content=content,
        n_storyboard=n_scenes,
        min_words=min_words,
        max_words=max_words
    )
    
    response = await llm_service(
        prompt=prompt,
        temperature=0.8,
        max_tokens=2000
    )
    
    # Parse JSON
    result = _parse_json(response)
    
    if "narrations" not in result:
        raise ValueError("Invalid response format: missing 'narrations' key")
    
    narrations = result["narrations"]
    
    # Validate count
    if len(narrations) > n_scenes:
        logger.warning(f"Got {len(narrations)} narrations, taking first {n_scenes}")
        narrations = narrations[:n_scenes]
    elif len(narrations) < n_scenes:
        raise ValueError(f"Expected {n_scenes} narrations, got only {len(narrations)}")
    
    logger.info(f"Generated {len(narrations)} narrations successfully")
    return narrations


async def split_narration_script(
    script: str,
    split_mode: Literal["paragraph", "line", "sentence"] = "paragraph",
) -> List[str]:
    """
    Split user-provided narration script into segments
    
    Args:
        script: Fixed narration script
        split_mode: Splitting strategy
            - "paragraph": Split by double newline (\\n\\n), preserve single newlines within paragraphs
            - "line": Split by single newline (\\n), each line is a segment
            - "sentence": Split by sentence-ending punctuation (。.!?！？)
    
    Returns:
        List of narration segments
    """
    logger.info(f"Splitting script (mode={split_mode}, length={len(script)} chars)")
    
    narrations = []
    
    if split_mode == "paragraph":
        # Split by double newline (paragraph mode)
        # Preserve single newlines within paragraphs
        paragraphs = re.split(r'\n\s*\n', script)
        for para in paragraphs:
            # Only strip leading/trailing whitespace, preserve internal newlines
            cleaned = para.strip()
            if cleaned:
                narrations.append(para)
        logger.info(f"✅ Split script into {len(narrations)} segments (by paragraph)")
    
    elif split_mode == "line":
        # Split by single newline (original behavior)
        narrations = [line.strip() for line in script.split('\n') if line.strip()]
        logger.info(f"✅ Split script into {len(narrations)} segments (by line)")
    
    elif split_mode == "sentence":
        # Split by sentence-ending punctuation
        # Supports Chinese (。！？) and English (.!?)
        # Use regex to split while keeping sentences intact
        cleaned = re.sub(r'\s+', ' ', script.strip())
        # Split on sentence-ending punctuation, keeping the punctuation with the sentence
        sentences = re.split(r'(?<=[。.!?！？])\s*', cleaned)
        narrations = [s.strip() for s in sentences if s.strip()]
        logger.info(f"✅ Split script into {len(narrations)} segments (by sentence)")
    
    else:
        # Fallback to line mode
        logger.warning(f"Unknown split_mode '{split_mode}', falling back to 'line'")
        narrations = [line.strip() for line in script.split('\n') if line.strip()]
    
    # Log statistics
    if narrations:
        lengths = [len(s) for s in narrations]
        logger.info(f"   Min: {min(lengths)} chars, Max: {max(lengths)} chars, Avg: {sum(lengths)//len(lengths)} chars")
    
    return narrations


async def generate_image_prompts(
    llm_service,
    narrations: List[str],
    min_words: int = 30,
    max_words: int = 60,
    batch_size: int = 10,
    max_retries: int = 3,
    progress_callback: Optional[callable] = None,
    content_style: ContentStyle = "general",
) -> List[str]:
    """
    Generate image prompts from narrations (with batching and retry)
    
    Args:
        llm_service: LLM service instance
        narrations: List of narrations
        min_words: Min image prompt length
        max_words: Max image prompt length
        batch_size: Max narrations per batch (default: 10)
        max_retries: Max retry attempts per batch (default: 3)
        progress_callback: Optional callback(completed, total, message) for progress updates
    
    Returns:
        List of image prompts (base prompts, without prefix applied)
    """
    from pixelle_video.prompts import build_image_prompt_prompt
    from pixelle_video.prompts.image_generation import build_tech_pop_image_prompt_prompt
    
    logger.info(
        f"Generating image prompts for {len(narrations)} narrations "
        f"(batch_size={batch_size}, style={content_style})"
    )
    
    # Split narrations into batches
    batches = [narrations[i:i + batch_size] for i in range(0, len(narrations), batch_size)]
    logger.info(f"Split into {len(batches)} batches")
    
    all_prompts = []
    
    # Process each batch
    for batch_idx, batch_narrations in enumerate(batches, 1):
        logger.info(f"Processing batch {batch_idx}/{len(batches)} ({len(batch_narrations)} narrations)")
        
        # Retry logic for this batch
        for attempt in range(1, max_retries + 1):
            try:
                # Generate prompts for this batch
                if content_style == "tech_pop":
                    prompt = build_tech_pop_image_prompt_prompt(
                        narrations=batch_narrations,
                        min_words=min_words,
                        max_words=max_words,
                    )
                else:
                    prompt = build_image_prompt_prompt(
                        narrations=batch_narrations,
                        min_words=min_words,
                        max_words=max_words,
                    )
                
                response = await llm_service(
                    prompt=prompt,
                    temperature=0.7,
                    max_tokens=8192
                )
                
                logger.debug(f"Batch {batch_idx} attempt {attempt}: LLM response length: {len(response)} chars")
                
                # Parse JSON
                result = _parse_json(response)
                
                if "image_prompts" not in result:
                    raise KeyError("Invalid response format: missing 'image_prompts'")
                
                batch_prompts = result["image_prompts"]
                
                # Validate count
                if len(batch_prompts) != len(batch_narrations):
                    error_msg = (
                        f"Batch {batch_idx} prompt count mismatch (attempt {attempt}/{max_retries}):\n"
                        f"  Expected: {len(batch_narrations)} prompts\n"
                        f"  Got: {len(batch_prompts)} prompts"
                    )
                    logger.warning(error_msg)
                    
                    if attempt < max_retries:
                        logger.info(f"Retrying batch {batch_idx}...")
                        continue
                    else:
                        raise ValueError(error_msg)
                
                # Success!
                logger.info(f"✅ Batch {batch_idx} completed successfully ({len(batch_prompts)} prompts)")
                all_prompts.extend(batch_prompts)
                
                # Report progress
                if progress_callback:
                    progress_callback(
                        len(all_prompts),
                        len(narrations),
                        f"Batch {batch_idx}/{len(batches)} completed"
                    )
                
                break
                
            except json.JSONDecodeError as e:
                logger.error(f"Batch {batch_idx} JSON parse error (attempt {attempt}/{max_retries}): {e}")
                if attempt >= max_retries:
                    raise
                logger.info(f"Retrying batch {batch_idx}...")
    
    logger.info(f"✅ Generated {len(all_prompts)} image prompts")
    return all_prompts


async def generate_video_prompts(
    llm_service,
    narrations: List[str],
    min_words: int = 30,
    max_words: int = 60,
    batch_size: int = 10,
    max_retries: int = 3,
    progress_callback: Optional[callable] = None
) -> List[str]:
    """
    Generate video prompts from narrations (with batching and retry)
    
    Args:
        llm_service: LLM service instance
        narrations: List of narrations
        min_words: Min video prompt length
        max_words: Max video prompt length
        batch_size: Max narrations per batch (default: 10)
        max_retries: Max retry attempts per batch (default: 3)
        progress_callback: Optional callback(completed, total, message) for progress updates
    
    Returns:
        List of video prompts (base prompts, without prefix applied)
    """
    from pixelle_video.prompts.video_generation import build_video_prompt_prompt
    
    logger.info(f"Generating video prompts for {len(narrations)} narrations (batch_size={batch_size})")
    
    # Split narrations into batches
    batches = [narrations[i:i + batch_size] for i in range(0, len(narrations), batch_size)]
    logger.info(f"Split into {len(batches)} batches")
    
    all_prompts = []
    
    # Process each batch
    for batch_idx, batch_narrations in enumerate(batches, 1):
        logger.info(f"Processing batch {batch_idx}/{len(batches)} ({len(batch_narrations)} narrations)")
        
        # Retry logic for this batch
        for attempt in range(1, max_retries + 1):
            try:
                # Generate prompts for this batch
                prompt = build_video_prompt_prompt(
                    narrations=batch_narrations,
                    min_words=min_words,
                    max_words=max_words
                )
                
                response = await llm_service(
                    prompt=prompt,
                    temperature=0.7,
                    max_tokens=8192
                )
                
                logger.debug(f"Batch {batch_idx} attempt {attempt}: LLM response length: {len(response)} chars")
                
                # Parse JSON
                result = _parse_json(response)
                
                if "video_prompts" not in result:
                    raise KeyError("Invalid response format: missing 'video_prompts'")
                
                batch_prompts = result["video_prompts"]
                
                # Validate batch result
                if len(batch_prompts) != len(batch_narrations):
                    raise ValueError(
                        f"Prompt count mismatch: expected {len(batch_narrations)}, got {len(batch_prompts)}"
                    )
                
                # Success - add to all_prompts
                all_prompts.extend(batch_prompts)
                logger.info(f"✓ Batch {batch_idx} completed: {len(batch_prompts)} video prompts")
                
                # Report progress
                if progress_callback:
                    completed = len(all_prompts)
                    total = len(narrations)
                    progress_callback(completed, total, f"Batch {batch_idx}/{len(batches)} completed")
                
                break  # Success, move to next batch
            
            except Exception as e:
                logger.warning(f"✗ Batch {batch_idx} attempt {attempt} failed: {e}")
                if attempt >= max_retries:
                    raise
                logger.info(f"Retrying batch {batch_idx}...")
    
    logger.info(f"✅ Generated {len(all_prompts)} video prompts")
    return all_prompts


def _parse_json(text: str) -> dict:
    """
    Parse JSON from text, with fallback to extract JSON from markdown code blocks
    
    Args:
        text: Text containing JSON
        
    Returns:
        Parsed JSON dict
        
    Raises:
        json.JSONDecodeError: If no valid JSON found
    """
    # Try direct parsing first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # Try to extract JSON from markdown code block
    json_pattern = r'```(?:json)?\s*([\s\S]+?)\s*```'
    match = re.search(json_pattern, text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    
    # Try to find any JSON object in the text
    json_pattern = r'\{[^{}]*(?:"narrations"|"image_prompts"|"scenes"|"video_prompts")\s*:\s*\[[^\]]*\][^{}]*\}'
    match = re.search(json_pattern, text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    
    # If all fails, raise error
    raise json.JSONDecodeError("No valid JSON found", text, 0)

