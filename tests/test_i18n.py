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
i18n guard tests.

AGENTS.md rule 4: UI copy must go through `tr()`, and zh_CN / en_US must stay in
lockstep. A missing key fails silently at runtime (tr() returns the key), so the
drift is invisible until a user sees raw `section.foo` in the UI. These tests make
it visible at CI time instead.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
LOCALES_DIR = REPO_ROOT / "web" / "i18n" / "locales"
REQUIRED_LOCALES = ["zh_CN", "en_US"]

# `tr("some.key")` / tr("some.key", fallback=...)
TR_CALL = re.compile(r"\btr\(\s*[\"']([a-zA-Z0-9_.\-]+)[\"']")


def _load(locale: str) -> dict:
    path = LOCALES_DIR / f"{locale}.json"
    assert path.is_file(), f"Missing locale file: {path}"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("t", {})


def test_all_locales_have_identical_keys():
    """zh_CN and en_US must define exactly the same keys."""
    tables = {locale: _load(locale) for locale in REQUIRED_LOCALES}

    reference = REQUIRED_LOCALES[0]
    for locale in REQUIRED_LOCALES[1:]:
        missing = sorted(set(tables[reference]) - set(tables[locale]))
        extra = sorted(set(tables[locale]) - set(tables[reference]))
        assert not missing and not extra, (
            f"Locale {locale} is out of sync with {reference} "
            f"(AGENTS.md rule 4: add new keys to both files).\n"
            f"Missing in {locale}: {missing[:20]}\n"
            f"Unexpected in {locale}: {extra[:20]}"
        )


def test_no_empty_translations():
    """An empty string renders as a blank label, which is worse than no key."""
    for locale in REQUIRED_LOCALES:
        empty = sorted(k for k, v in _load(locale).items() if not str(v).strip())
        assert not empty, f"{locale} has empty translations: {empty[:20]}"


def _code_lines(source: str):
    """Yield lines that are actual code, skipping comments and docstrings."""
    in_docstring = False
    for line in source.splitlines():
        stripped = line.strip()
        if in_docstring:
            if '"""' in line or "'''" in line:
                in_docstring = False
            continue
        if stripped.startswith(('"""', "'''")):
            if line.count('"""') % 2 == 1 or line.count("'''") % 2 == 1:
                in_docstring = True
            continue
        if stripped.startswith("#"):
            continue
        yield line


def test_tr_keys_used_in_ui_exist():
    """Every literal key passed to tr() must exist in the locale files."""
    tables = {locale: _load(locale) for locale in REQUIRED_LOCALES}
    known = set(tables[REQUIRED_LOCALES[0]])

    unknown = {}
    for path in (REPO_ROOT / "web").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        code = "\n".join(_code_lines(source))
        for key in TR_CALL.findall(code):
            if key not in known:
                rel = path.relative_to(REPO_ROOT).as_posix()
                unknown.setdefault(rel, set()).add(key)

    assert not unknown, (
        "tr() called with keys that no locale defines (they render as the raw "
        "key). Add them to both locale files.\n"
        + "\n".join(f"  {rel}: {sorted(keys)}" for rel, keys in unknown.items())
    )
