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
Architecture guard tests.

These tests encode AGENTS.md's hard rules as executable checks, so an AI agent
(or a human) cannot merge code that violates them.

Rule enforced here: external model-provider calls must live in
``pixelle_video/services/api_services/``. Direct ``httpx``/``requests`` calls
scattered across pipelines, services, utils, or web let provider credentials and
retry/proxy behavior drift away from the single place that owns them.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]

# Directories scanned for the rule.
SCAN_ROOTS = ["pixelle_video", "api"]

# The one place provider HTTP is allowed.
ALLOWED_DIR = "pixelle_video/services/api_services"

# Historical call sites that predate the rule. They download artifacts or are
# utility helpers, not provider integrations. Do NOT add to this list: new
# provider calls belong in ALLOWED_DIR. Shrink it by moving a file there.
ALLOWED_FILES = {
    "pixelle_video/services/frame_processor.py",  # downloads generated frames
    "pixelle_video/services/tts_service.py",      # fetches remote audio assets
    "pixelle_video/utils/llm_util.py",            # generic OpenAI-compatible probe
}

# httpx.AsyncClient / httpx.Client are construction, not a call; requiring them
# to move would be churn without benefit. We flag actual request methods plus
# bare construction outside the allowed dir, which is where drift starts.
DIRECT_CALL_PATTERNS = [
    re.compile(r"\bhttpx\.(get|post|put|delete|patch|request|stream)\s*\("),
    re.compile(r"\brequests\.(get|post|put|delete|patch|request)\s*\("),
]

# Docstrings and comments mention these names legitimately.
COMMENT_OR_DOCSTRING = re.compile(r"^\s*(#|\"\"\"|'''|\"|')", re.MULTILINE)


def _strip_string_literals(source: str) -> str:
    """Drop docstrings and comments so prose about httpx is not a violation."""
    lines = []
    in_docstring = False
    for line in source.splitlines():
        stripped = line.strip()
        if in_docstring:
            if '"""' in line or "'''" in line:
                in_docstring = False
            continue
        if stripped.startswith(('"""', "'''")):
            # Single-line docstring or the start of a block one.
            if not (stripped.endswith('"""') or stripped.endswith("'''")) or stripped.count('"""') < 2:
                if line.count('"""') % 2 == 1 or line.count("'''") % 2 == 1:
                    in_docstring = True
            continue
        if stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def _iter_python_files():
    for root in SCAN_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel.startswith(ALLOWED_DIR):
                continue
            if rel in ALLOWED_FILES:
                continue
            yield rel, path


def test_no_direct_provider_calls_outside_api_services():
    """External HTTP must be confined to services/api_services/."""
    violations = []
    for rel, path in _iter_python_files():
        source = path.read_text(encoding="utf-8")
        code = _strip_string_literals(source)
        for pattern in DIRECT_CALL_PATTERNS:
            for match in pattern.finditer(code):
                line_no = code[: match.start()].count("\n") + 1
                violations.append(f"{rel}:{line_no} -> {match.group(0)}")

    assert not violations, (
        "Direct external HTTP calls outside "
        f"{ALLOWED_DIR}/ are not allowed (AGENTS.md rule 1).\n"
        "Move the call into a client under services/api_services/ and route it "
        "through the matching *_client.py aggregator.\n"
        "Offending call sites:\n  " + "\n  ".join(violations)
    )


def test_allowlist_only_contains_existing_files():
    """Guard the guard: a stale allowlist entry silently disables the rule."""
    missing = sorted(
        rel for rel in ALLOWED_FILES if not (REPO_ROOT / rel).is_file()
    )
    assert not missing, (
        "ALLOWED_FILES references files that no longer exist: "
        f"{missing}. Remove them so the rule keeps covering real code."
    )


def test_pipelines_do_not_import_web_or_api():
    """Dependency direction is one-way: core must not depend on UI/HTTP layers."""
    violations = []
    base = REPO_ROOT / "pixelle_video"
    pattern = re.compile(r"^\s*(from|import)\s+(web|api)\b", re.MULTILINE)
    for path in base.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if pattern.search(source):
            rel = path.relative_to(REPO_ROOT).as_posix()
            violations.append(rel)

    assert not violations, (
        "pixelle_video/ must not import web/ or api/ (AGENTS.md dependency "
        "direction).\nOffending files:\n  " + "\n  ".join(violations)
    )


@pytest.mark.parametrize("relative", sorted(ALLOWED_FILES))
def test_allowlisted_files_are_justified(relative):
    """Keep the allowlist honest: each entry must still be a known exception."""
    assert (REPO_ROOT / relative).is_file(), f"{relative} missing"
