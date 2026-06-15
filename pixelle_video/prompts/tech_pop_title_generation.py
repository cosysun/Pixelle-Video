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
Tech popularization title generation prompt
"""

TECH_POP_TITLE_GENERATION_PROMPT = """Generate a short, curiosity-driven title for a computer science科普 video.

Content:
{content}

Requirements:
1. **Language**: MUST match the input content language
2. **Length**: MUST NOT exceed {max_length} characters
3. **Content**: Name the specific concept AND spark curiosity (e.g. "CPU和GPU到底差在哪")
4. **Forbidden**: No clickbait ("必看", "震惊", "99%的人不知道", "you won't believe")
5. **No trailing punctuation**
6. Output ONLY the title text, no quotes or explanation

Title:"""


def build_tech_pop_title_prompt(content: str, max_length: int = 22) -> str:
    """Build tech popularization title prompt with longer character limit."""
    return TECH_POP_TITLE_GENERATION_PROMPT.format(
        content=content[:500],
        max_length=max_length,
    )
