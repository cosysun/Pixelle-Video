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
Tech popularization narration generation prompts

For generating computer science科普 scripts targeting complete beginners.
"""

import json


# Shared quality rules injected into outline, narration, and review prompts.
TECH_POP_QUALITY_STANDARDS = """# Popular Science Quality Standards (科普质量标准)

## Explain, don't just metaphorize
- Analogies help intuition, but CANNOT replace stating how the thing actually works
- For "什么是…" / "what is …" topics: at least ONE explain scene MUST describe the core mechanism in plain language (what the system step-by-step DOES internally)
- Definition scene must state what category the concept belongs to AND its scope (what it includes / excludes)

## Scope boundaries (keep concepts precise)
- Do NOT lump unrelated technologies into one label unless the topic explicitly covers them
- Default: 大模型 / large model = 大语言模型 (Large Language Model), mainly text-in-text-out
- Image generation, video, voice cloning often use different model types; if mentioned, say they are separate or combined systems — do NOT imply one model does everything by default
- Multimodal models may read images, but classic LLM training is primarily on text — do not say "海量文字和图片" unless the topic is explicitly about multimodal models

## Common misconceptions — NEVER write these (rewrite if present)
- Parameters are NOT drawers that "store knowledge/facts" — they are numeric settings learned from data, used to compute outputs from inputs
- The model does NOT truly "understand" or "feel" — it matches statistical patterns in language
- Context memory is NOT long-term personal memory — it only uses text within the current conversation window; a new chat usually starts fresh
- Do NOT claim "无需重新学习就能做一切" — one pretrained model handles many tasks via prompts or fine-tuning, not zero adaptation for every skill
- Avoid heavy anthropomorphism ("像朋友一样倾听", "认真记住你") that contradicts later limitation disclaimers

## AI / 大模型 topics — when topic mentions 大模型, LLM, ChatGPT, or AI chat
- MUST include core mechanism: based on prior text, predict the next word/token one step at a time (接龙 / 猜下一个词), assembling the full reply
- Scene 2 definition should name it as a language-focused AI program trained on massive text
- Limitation scene should honestly state: pattern matching, not human-like understanding
"""


TECH_POP_NARRATION_PROMPT = """# Role Definition
You are a computer science popularization creator targeting complete beginners with zero technical background.
Your job is to explain technical concepts clearly and accurately — NOT to inspire emotions or share life philosophy.
Strictly output copy in the same language as the user's input topic.

# Core Task
Create {n_storyboard} video storyboard narrations for the topic below.
Each narration is spoken aloud by TTS — write for the ear, not for reading on paper.

# Input Topic
{topic}

# Storyboard Structure (MUST follow in order)
Assign each storyboard a role based on its position:

| Position | Role | What to write |
|----------|------|---------------|
| 1 | Hook | A relatable daily-life question or surprising fact. NO motivational openings. |
| 2 | Definition | One-sentence plain-language definition. NO jargon stacking. |
| 3 to N-2 | Explain | ONE concept per storyboard. MUST include a real-life analogy. |
| N-1 | Misconception or Importance | Common mistake OR why this matters in daily life. |
| N | Summary | One memorable takeaway sentence. NO "go take action" endings. |

If {n_storyboard} is less than 5, merge middle roles but keep Hook → Definition → Summary.

# Narration Specifications
- Language: Match the input topic language exactly (Chinese in → Chinese out, etc.)
- Length: Strictly {min_words}~{max_words} characters per narration (Chinese count by character, English count by word)
- Tone: Friendly teacher explaining to a friend, NOT a motivational speaker
- Punctuation: Use natural spoken punctuation within sentences; no trailing punctuation at the very end of each narration
- One new technical term per narration at most; when introduced, explain it immediately in the same sentence

# Accuracy Rules
- Only state textbook-level or widely accepted facts
- If uncertain, use hedging: "可以简单理解为…" / "you can think of it as…"
- NEVER fabricate statistics, version numbers, paper citations, or company claims
- NEVER cite psychology, philosophy, or self-help sources

{quality_standards}

# Strictly Forbidden
- Motivational endings ("行动起来", "加油", "you can do it")
- Template openings ("你知道吗", "Have you ever wondered", "想象一下")
- Unsubstantiated claims ("研究表明", "scientists proved")
- Emotional/philosophical content unrelated to the technical topic
- Stacking multiple unexplained jargon terms in one narration
- Empty statements with no concrete information
- Metaphor-only explain scenes with no statement of actual mechanism
- Conflating LLM with image/video/voice AI without clarifying scope

# Output Format
Output ONLY valid JSON, no other text:

```json
{{
  "narrations": [
    "First narration",
    "Second narration"
  ]
}}
```

# Checklist before output
1. Exactly {n_storyboard} narrations in the array
2. Each narration is {min_words}~{max_words} characters/words
3. Structure follows Hook → Definition → Explain → Misconception/Importance → Summary
4. At least one explain narration states the core mechanism, not only an analogy
5. No forbidden patterns or common misconceptions above
6. Language matches input topic

Now create {n_storyboard} narrations for the topic. Only output JSON.
"""


TECH_POP_OUTLINE_PROMPT = """# Role
You are planning a computer science科普 video script for complete beginners.

# Topic
{topic}

{quality_standards}

# Task
Create an outline with exactly {n_storyboard} scenes. Each scene must have:
- role: one of "hook", "definition", "explain", "misconception", "importance", "summary"
- key_point: the single fact or concept for this scene (one sentence, plain language)
- analogy: a real-life analogy to use (required for "explain" role; optional for others)
- forbidden_terms: jargon to avoid or must explain if used

# Planning rules
- Scene 2 key_point MUST define scope: what the concept is AND what it is not (if easily confused with related tech)
- Exactly ONE explain scene MUST have key_point describing the core mechanism (internal step-by-step behavior), not just a benefit or comparison
- Scene {n_storyboard_minus_1} key_point SHOULD name a specific common misconception to debunk (preferred over generic "why it matters")
- Do NOT plan scenes that conflate unrelated technologies unless the topic explicitly requires it

# Structure rules
- Scene 1: role = hook
- Scene 2: role = definition
- Scenes 3 to {n_storyboard_minus_2}: role = explain (one concept each)
- Scene {n_storyboard_minus_1}: role = misconception OR importance
- Scene {n_storyboard}: role = summary

# Output
Output ONLY valid JSON:

```json
{{
  "scenes": [
    {{
      "scene_number": 1,
      "role": "hook",
      "key_point": "...",
      "analogy": "...",
      "forbidden_terms": "..."
    }}
  ]
}}
```

Language for key_point and analogy: match the input topic language.
Only output JSON.
"""


TECH_POP_NARRATIONS_FROM_OUTLINE_PROMPT = """# Role
You are writing spoken narrations for a computer science科普 video targeting complete beginners.

# Topic
{topic}

# Outline (follow strictly — do NOT deviate)
{outline_json}

# Task
Write one narration per scene in the outline. Each narration:
- Length: {min_words}~{max_words} characters/words
- Implements the key_point and uses the analogy where provided
- For the scene whose key_point describes core mechanism: state the mechanism plainly FIRST, then optionally add the analogy in the same narration
- Avoids forbidden_terms unless explained immediately
- Natural spoken tone, no motivational language
- Language matches the topic language

{quality_standards}

# Output
Output ONLY valid JSON:

```json
{{
  "narrations": [
    "First narration",
    "Second narration"
  ]
}}
```

The narrations array must have exactly {n_storyboard} elements, one per scene in order.
Only output JSON.
"""


TECH_POP_REVIEW_PROMPT = """# Role
You are a fact-checker and editor for computer science科普 video scripts.
Your bar: after watching, a beginner can accurately explain the topic to a friend — not just recall metaphors.

# Topic
{topic}

# Draft narrations
{narrations_json}

{quality_standards}

# Task
Review the FULL script first, then each narration individually.

Script-level checks (fix across narrations if needed):
- Does the script include the core mechanism in plain language (not only analogies)?
- Are scope boundaries clear (what the concept is vs. what it is not)?
- Any conflation of LLM with image/video/voice AI without clarification?
- Any contradiction (e.g., anthropomorphic "friend who remembers you" vs. later "no real understanding")?

Per-narration checks:
1. Is the technical content accurate (no fabrication)?
2. Are jargon terms explained on first use?
3. Does it avoid motivational/philosophical tone?
4. Is it {min_words}~{max_words} characters/words?
5. Does it contain concrete information, not empty filler?
6. Does it avoid the common misconceptions listed above?

If a narration passes all checks, keep it unchanged.
If it fails, rewrite ONLY that narration to fix the issue.
Preserve storyboard order and role intent (hook → definition → explain → misconception → summary).

# Output
Output ONLY valid JSON:

```json
{{
  "narrations": [
    "First narration (original or rewritten)",
    "Second narration"
  ]
}}
```

Must output exactly {n_storyboard} narrations. Only output JSON.
"""


def _assign_storyboard_roles(n_storyboard: int) -> str:
    """Build human-readable role assignment hint for small scene counts."""
    if n_storyboard >= 5:
        return (
            f"- Scene 1: hook\n"
            f"- Scene 2: definition\n"
            f"- Scenes 3 to {n_storyboard - 2}: explain (one concept + analogy each)\n"
            f"- Scene {n_storyboard - 1}: misconception or importance\n"
            f"- Scene {n_storyboard}: summary"
        )
    roles = ["hook", "definition"]
    for i in range(3, n_storyboard):
        roles.append("explain" if i < n_storyboard else "summary")
    if n_storyboard >= 3:
        roles[-1] = "summary"
    lines = [f"- Scene {i + 1}: {roles[i]}" for i in range(n_storyboard)]
    return "\n".join(lines)


def build_tech_pop_narration_prompt(
    topic: str,
    n_storyboard: int,
    min_words: int,
    max_words: int,
) -> str:
    """Build single-pass tech popularization narration prompt."""
    return TECH_POP_NARRATION_PROMPT.format(
        topic=topic,
        n_storyboard=n_storyboard,
        min_words=min_words,
        max_words=max_words,
        quality_standards=TECH_POP_QUALITY_STANDARDS,
    )


def build_tech_pop_outline_prompt(
    topic: str,
    n_storyboard: int,
) -> str:
    """Build outline prompt for two-pass tech script generation."""
    return TECH_POP_OUTLINE_PROMPT.format(
        topic=topic,
        n_storyboard=n_storyboard,
        n_storyboard_minus_2=max(3, n_storyboard - 2),
        n_storyboard_minus_1=max(2, n_storyboard - 1),
        quality_standards=TECH_POP_QUALITY_STANDARDS,
    )


def build_tech_pop_narrations_from_outline_prompt(
    topic: str,
    outline: list,
    n_storyboard: int,
    min_words: int,
    max_words: int,
) -> str:
    """Build narrations-from-outline prompt for pass 2."""
    return TECH_POP_NARRATIONS_FROM_OUTLINE_PROMPT.format(
        topic=topic,
        outline_json=json.dumps({"scenes": outline}, ensure_ascii=False, indent=2),
        n_storyboard=n_storyboard,
        min_words=min_words,
        max_words=max_words,
        quality_standards=TECH_POP_QUALITY_STANDARDS,
    )


def build_tech_pop_review_prompt(
    topic: str,
    narrations: list,
    n_storyboard: int,
    min_words: int,
    max_words: int,
) -> str:
    """Build review/rewrite prompt for pass 3."""
    return TECH_POP_REVIEW_PROMPT.format(
        topic=topic,
        narrations_json=json.dumps({"narrations": narrations}, ensure_ascii=False, indent=2),
        n_storyboard=n_storyboard,
        min_words=min_words,
        max_words=max_words,
        quality_standards=TECH_POP_QUALITY_STANDARDS,
    )
