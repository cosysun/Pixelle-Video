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
Retry helper for transient failures in pipeline steps.

Many TTS / media-generation calls fail with transient errors that succeed on
retry: ComfyUI workflow returning no audio, network timeouts, RunningHub queue
hiccups, etc. ``with_retry`` wraps a single async call with bounded retries so
the pipeline doesn't have to surface those to the user.

Permanent errors (auth, validation, missing files) are NOT retried — they fall
through immediately so the pipeline can persist the failure and stop.

Usage::

    from pixelle_video.utils.retry import with_retry

    audio_path = await with_retry(
        lambda: self.core.tts(**tts_params),
        label="tts",
    )
"""

import asyncio
import errno as _errno
from typing import Awaitable, Callable, Sequence, TypeVar

from loguru import logger

T = TypeVar("T")

# Defaults. Kept conservative so the worst case is ~20s of waiting on a
# permanent failure. Per-call overrides are accepted in ``with_retry``.
DEFAULT_MAX_ATTEMPTS: int = 3
DEFAULT_DELAYS_SECONDS: Sequence[float] = (2.0, 5.0, 12.0)

# Substrings (case-insensitive) that mark an exception as transient.
# Intentionally narrow — we don't want to retry validation errors.
_TRANSIENT_MESSAGE_SUBSTRINGS: tuple[str, ...] = (
    "no audio file generated",
    "tts generation failed",
    "timeout",
    "timed out",
    "connection",
    "econnreset",
    "queue",
    "503",
    "502",
    "504",
    "temporarily unavailable",
    "remote disconnected",
    "read error",
)


# OSError errnos that indicate a transient network/socket condition. Everything
# else (FileNotFoundError → ENOENT, PermissionError → EACCES, etc.) is permanent
# and should NOT be retried.
_TRANSIENT_OS_ERRNOS: frozenset[int] = frozenset(
    e for e in (
        getattr(_errno, "ECONNRESET", None),
        getattr(_errno, "ETIMEDOUT", None),
        getattr(_errno, "ECONNABORTED", None),
        getattr(_errno, "ENETUNREACH", None),
        getattr(_errno, "EHOSTUNREACH", None),
        getattr(_errno, "EPIPE", None),
        getattr(_errno, "ECONNREFUSED", None),
    )
    if e is not None
)


def is_transient_error(exc: BaseException) -> bool:
    """
    Return True if ``exc`` looks like something worth retrying.

    Matches on common transport exception types from httpx / asyncio /
    builtins, and on substrings inside the exception message (case-
    insensitive). Validation/structural exceptions (ValueError, KeyError,
    FileNotFoundError, etc.) are NOT transient.
    """
    # Type-based: catch the obvious transport errors. ``ConnectionError`` is
    # the umbrella for ``ConnectionResetError`` / ``BrokenPipeError`` /
    # ``ConnectionAbortedError`` / ``ConnectionRefusedError``.
    if isinstance(exc, (asyncio.TimeoutError, ConnectionError)):
        return True

    # ``OSError`` is the parent of FileNotFoundError, IsADirectoryError,
    # PermissionError, etc — none of which are transient. Only retry if the
    # errno is one of the known network-flap codes.
    if isinstance(exc, OSError) and exc.errno in _TRANSIENT_OS_ERRNOS:
        return True

    # httpx is used heavily in this codebase. Match by class name to avoid a
    # hard import dependency on this util module.
    exc_type_name = type(exc).__name__
    if exc_type_name in {
        "HTTPError",
        "HTTPStatusError",
        "RequestError",
        "TimeoutException",
        "ConnectError",
        "ReadError",
        "ReadTimeout",
        "WriteError",
        "PoolTimeout",
        "RemoteProtocolError",
    }:
        return True

    # Message-based fallback for generic Exceptions raised by ComfyKit /
    # provider clients with informative strings.
    msg = str(exc).lower()
    return any(needle in msg for needle in _TRANSIENT_MESSAGE_SUBSTRINGS)


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    delays: Sequence[float] = DEFAULT_DELAYS_SECONDS,
    retryable: Callable[[BaseException], bool] = is_transient_error,
    label: str = "operation",
) -> T:
    """
    Call ``fn`` with bounded retries on transient errors.

    Args:
        fn: A zero-arg async callable. Wrap your call with a lambda::

                lambda: self.core.tts(**params)

        max_attempts: Max total attempts (including the first). Must be >= 1.
        delays: Sleep durations *between* attempts. Indexed by 0-based attempt
            number after the first failure. If shorter than ``max_attempts-1``,
            the final value is reused.
        retryable: Predicate that decides whether a given exception is worth
            retrying. Default: :func:`is_transient_error`.
        label: Short human-readable label for logs (e.g. ``"tts"``, ``"media"``).

    Returns:
        Whatever ``fn`` returns on the first successful call.

    Raises:
        The last exception from ``fn`` if all attempts fail, OR the first
        non-transient exception encountered.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except BaseException as exc:  # noqa: BLE001 — we re-raise below
            last_exc = exc

            # Permanent errors fall through immediately.
            if not retryable(exc):
                logger.debug(f"❌ {label}: non-transient {type(exc).__name__}, not retrying")
                raise

            # Last attempt: don't sleep, just give up.
            if attempt >= max_attempts:
                logger.error(
                    f"❌ {label}: failed after {attempt}/{max_attempts} attempts "
                    f"({type(exc).__name__}: {exc})"
                )
                raise

            # Pick a delay; reuse the last one if delays is shorter.
            delay_idx = min(attempt - 1, len(delays) - 1)
            delay = delays[delay_idx] if delays else 0.0

            logger.warning(
                f"⏳ {label}: attempt {attempt}/{max_attempts} failed "
                f"({type(exc).__name__}: {exc}). Retrying in {delay:.1f}s..."
            )
            if delay > 0:
                await asyncio.sleep(delay)

    # Defensive: the loop always either returns or raises, but mypy/pyright
    # might not see that. last_exc is guaranteed non-None here.
    assert last_exc is not None
    raise last_exc
