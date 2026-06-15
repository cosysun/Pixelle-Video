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
Image prompt generation template

For generating image prompts from narrations.
"""

import json
from typing import List, Optional


# ==================== PRESET IMAGE STYLES ====================
# Predefined visual styles for different use cases

IMAGE_STYLE_PRESETS = {
    "stick_figure": {
        "name": "Stick Figure Sketch",
        "description": "stick figure style sketch, black and white lines, pure white background, minimalist hand-drawn feel",
        "use_case": "General scenes, simple and intuitive"
    },
    
    "minimal": {
        "name": "Minimalist Abstract",
        "description": "minimalist abstract art, geometric shapes, clean composition, modern design, soft pastel colors",
        "use_case": "Modern, artistic feel"
    },
    
    "concept": {
        "name": "Conceptual Visual",
        "description": "conceptual visual metaphors, symbolic elements, thought-provoking imagery, artistic interpretation",
        "use_case": "Deep content, philosophical thinking"
    },

    "tech_diagram": {
        "name": "Tech Infographic",
        "description": "flat infographic style, clean diagram, flowchart elements, labeled arrows with Simplified Chinese text, simple icons, educational illustration, white or light background, no photorealism, technical terms may use English abbreviations",
        "use_case": "Computer science科普, technical concepts with real-life analogies"
    },
}

# Policy for visible text rendered inside generated images (shared by prompt templates)
ON_IMAGE_TEXT_POLICY = """## On-Image Text (Labels & Annotations)
- Target audience: Chinese domestic viewers — minimize English-only labels
- Any visible text inside the image (titles, arrow labels, step numbers, captions) MUST be in **Simplified Chinese (简体中文)**
- Standard technical/professional terms may stay in English when widely used (e.g. DNS, API, HTTP, CPU, GPU, URL, IP, SQL)
- Prefer mixed form for teaching: Chinese explanation + English term (e.g. label "DNS 域名解析" instead of "Domain Name Resolution")
- In each English image prompt, **spell out the exact Chinese characters** to render (e.g. `label reading "邮件分拣中心"`, `arrow labeled "步骤一"`)
- Keep on-image text minimal, large, and readable — at most 3-5 short labels per image
- Do NOT use long English sentences or paragraphs as on-image text"""

# Default preset
DEFAULT_IMAGE_STYLE = "stick_figure"


IMAGE_PROMPT_GENERATION_PROMPT = """# Role Definition
You are a professional visual creative designer, skilled at creating expressive and symbolic image prompts for video scripts, transforming abstract concepts into concrete visual scenes.

# Core Task
Based on the existing video script, create corresponding **English** image prompts for each storyboard's "narration content", ensuring visual scenes perfectly match the narrative content and enhance audience understanding and memory.

**Important: The input contains {narrations_count} narrations. You must generate one corresponding image prompt for each narration, totaling {narrations_count} image prompts.**

# Input Content
{narrations_json}

# Output Requirements

## Image Prompt Specifications
- Language: **Must use English** (for AI image generation models)
- Description structure: scene + character action + emotion + symbolic elements
- Description length: Ensure clear, complete, and creative descriptions (recommended 50-100 English words)

## Visual Creative Requirements
- Each image must accurately reflect the specific content and emotion of the corresponding narration
- Use symbolic techniques to visualize abstract concepts (e.g., use paths to represent life choices, chains to represent constraints, etc.)
- Scenes should express rich emotions and actions to enhance visual impact
- Highlight themes through composition and element arrangement, avoid overly literal representations

{on_image_text_policy}

## Key English Vocabulary Reference
- Symbolic elements: symbolic elements
- Expression: expression / facial expression
- Action: action / gesture / movement
- Scene: scene / setting
- Atmosphere: atmosphere / mood

## Visual and Copy Coordination Principles
- Images should serve the copy, becoming a visual extension of the copy content
- Avoid visual elements unrelated to or contradicting the copy content
- Choose visual presentation methods that best enhance the persuasiveness of the copy
- Ensure the audience can quickly understand the core viewpoint of the copy through images

## Creative Guidance
1. **Phenomenon Description Copy**: Use intuitive scenes to represent social phenomena
2. **Cause Analysis Copy**: Use visual metaphors of cause-and-effect relationships to represent internal logic
3. **Impact Argumentation Copy**: Use consequence scenes or contrast techniques to represent the degree of impact
4. **In-depth Discussion Copy**: Use concretization of abstract concepts to represent deep thinking
5. **Conclusion Inspiration Copy**: Use open-ended scenes or guiding elements to represent inspiration

# Output Format
Strictly output in the following JSON format, **image prompts must be in English**:

```json
{{
  "image_prompts": [
    "[detailed English image prompt following the style requirements]",
    "[detailed English image prompt following the style requirements]"
  ]
}}
```

# Important Reminders
1. Only output JSON format content, do not add any explanations
2. Ensure JSON format is strictly correct and can be directly parsed by the program
3. Input is {{"narrations": [narration array]}} format, output is {{"image_prompts": [image prompt array]}} format
4. **The output image_prompts array must contain exactly {narrations_count} elements, corresponding one-to-one with the input narrations array**
5. **Image prompts must use English** (for AI image generation models)
6. Image prompts must accurately reflect the specific content and emotion of the corresponding narration
7. Each image must be creative and visually impactful, avoid being monotonous
8. Ensure visual scenes can enhance the persuasiveness of the copy and audience understanding

Now, please create {narrations_count} corresponding **English** image prompts for the above {narrations_count} narrations. Only output JSON, no other content.
"""


TECH_POP_IMAGE_PROMPT_GENERATION_PROMPT = """# Role Definition
You are a visual designer for computer science科普 videos targeting complete beginners.
Create **English** image prompts that visualize technical concepts through real-life analogies and simple diagrams.

**Important: The input contains {narrations_count} narrations. Generate exactly {narrations_count} image prompts.**

# Input Content
{narrations_json}

# Image Prompt Specifications
- Language: **English only** (for AI image models) — but on-image labels must be Simplified Chinese (see below)
- Style: flat infographic, simple diagram, flowchart, comparison chart, or analogy illustration
- Length: 50-100 English words per prompt
- Visualize the **concrete analogy** in the narration (e.g. DNS → mail sorting center, arrow labeled "DNS 域名解析", box labeled "邮件分拣中心")
- Prefer: labeled arrows with Chinese text, simple icons, before/after comparison, step-by-step flow
- Avoid: abstract emotional scenes, philosophical metaphors, photorealistic people, dark moody atmosphere

{on_image_text_policy}

# Rules
- Each image must match its narration's specific teaching point
- Use educational clarity over artistic ambiguity
- If narration mentions an analogy, draw THAT analogy literally
- No text-heavy screenshots or unreadable UI mockups

# Output Format
```json
{{
  "image_prompts": [
    "[English infographic-style image prompt]",
    "[English infographic-style image prompt]"
  ]
}}
```

Output exactly {narrations_count} prompts. Only output JSON.
"""


def build_image_prompt_prompt(
    narrations: List[str],
    min_words: int,
    max_words: int
) -> str:
    """
    Build image prompt generation prompt
    
    Note: Style/prefix will be applied later via prompt_prefix in config.
    
    Args:
        narrations: List of narrations
        min_words: Minimum word count
        max_words: Maximum word count
    
    Returns:
        Formatted prompt for LLM
    
    Example:
        >>> build_image_prompt_prompt(narrations, 50, 100)
    """
    narrations_json = json.dumps(
        {"narrations": narrations},
        ensure_ascii=False,
        indent=2
    )
    
    return IMAGE_PROMPT_GENERATION_PROMPT.format(
        narrations_json=narrations_json,
        narrations_count=len(narrations),
        min_words=min_words,
        max_words=max_words,
        on_image_text_policy=ON_IMAGE_TEXT_POLICY,
    )


def build_tech_pop_image_prompt_prompt(
    narrations: List[str],
    min_words: int,
    max_words: int,
) -> str:
    """Build image prompt generation prompt for tech popularization content."""
    narrations_json = json.dumps(
        {"narrations": narrations},
        ensure_ascii=False,
        indent=2,
    )
    return TECH_POP_IMAGE_PROMPT_GENERATION_PROMPT.format(
        narrations_json=narrations_json,
        narrations_count=len(narrations),
        min_words=min_words,
        max_words=max_words,
        on_image_text_policy=ON_IMAGE_TEXT_POLICY,
    )

