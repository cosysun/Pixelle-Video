"""Helpers for suppressing duplicate Streamlit edit submissions."""


def is_recent_duplicate_action(
    *,
    last_completed_at: float | None,
    now: float,
    window_seconds: float = 30.0,
) -> bool:
    """Return True when the same edit action completed too recently."""
    if last_completed_at is None:
        return False
    return 0 <= now - last_completed_at < window_seconds
