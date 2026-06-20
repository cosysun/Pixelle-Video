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
Narration splitter for subtitle chunking.

When a storyboard frame's narration is longer than the bottom text card can
hold (the fixed-height container in templates/1080x1920/*.html fits roughly
21 Chinese characters), we split it into smaller "subtitle chunks". Each
chunk becomes its own sub-frame sharing the parent's image but with its own
TTS audio - the same image plays while subtitles change underneath.

Splitting is deterministic, code-only (no LLM), and aligns to Chinese
punctuation boundaries.
"""

from loguru import logger

# Sentence-final punctuation: prefer to split here.
_PRIMARY_PUNCT = "。！？；!?;"
# Clause-internal punctuation: fall back to these when a single sentence is
# already longer than max_chars.
_SECONDARY_PUNCT = "，、,"


def _split_keep_terminators(text: str, terminators: str) -> list[str]:
    """
    Split *text* into pieces such that each piece ends with one of the
    *terminators* (or is the trailing remainder). The terminator stays attached
    to the piece that contains the preceding text.

    Example:
        _split_keep_terminators("好。坏。妙", "。") == ["好。", "坏。", "妙"]
    """
    if not text:
        return []
    pieces: list[str] = []
    start = 0
    for i, ch in enumerate(text):
        if ch in terminators:
            pieces.append(text[start : i + 1])
            start = i + 1
    if start < len(text):
        pieces.append(text[start:])
    return pieces


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Last-resort split at exact max_chars boundaries."""
    if not text:
        return []
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def _greedy_pack(pieces: list[str], max_chars: int) -> list[str]:
    """
    Greedily concatenate consecutive *pieces* into chunks <= max_chars.
    Pieces longer than max_chars are emitted on their own (caller decides
    whether to subdivide them further).
    """
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if not piece:
            continue
        if not current:
            current = piece
            continue
        if len(current) + len(piece) <= max_chars:
            current += piece
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def split_narration_into_chunks(text: str, max_chars: int = 35) -> list[str]:
    """
    Split *text* into chunks of at most *max_chars* characters, preferring
    Chinese punctuation boundaries.

    Strategy:
      1. If text is empty/whitespace-only, return [].
      2. If len(text) <= max_chars, return [text] unchanged.
      3. Split on primary punctuation (。！？；) into "sentences", greedily
         pack them into chunks <= max_chars.
      4. If any chunk is still > max_chars (because a single sentence is
         already too long), recursively split that chunk on secondary
         punctuation (，、) and pack again.
      5. If still > max_chars, hard-split at character boundaries (logged).

    Returns a list of non-empty chunks; characters are not lost.

    Args:
        text: Narration text to split.
        max_chars: Maximum allowed characters per chunk.

    Returns:
        List of chunks, each <= max_chars (best-effort), preserving order.
    """
    if not text or not text.strip():
        return []
    if len(text) <= max_chars:
        return [text]

    # Step 1: split on primary punctuation, greedy-pack.
    sentences = _split_keep_terminators(text, _PRIMARY_PUNCT)
    chunks = _greedy_pack(sentences, max_chars)

    # Step 2: any chunk still too long -> split on secondary punctuation.
    refined: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            refined.append(chunk)
            continue

        clauses = _split_keep_terminators(chunk, _SECONDARY_PUNCT)
        sub_chunks = _greedy_pack(clauses, max_chars)

        # Step 3: still too long -> hard split.
        for sub in sub_chunks:
            if len(sub) <= max_chars:
                refined.append(sub)
            else:
                logger.warning(
                    f"split_narration_into_chunks: hard-splitting "
                    f"{len(sub)}-char segment with no usable punctuation: {sub!r}"
                )
                refined.extend(_hard_split(sub, max_chars))

    # Drop any empty pieces (shouldn't happen, but defensive).
    return [c for c in refined if c]
