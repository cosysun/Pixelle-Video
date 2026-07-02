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


def is_action_in_flight(state, action_key: str) -> bool:
    """Return True when the same edit action is already running."""
    return bool(state.get(_in_flight_key(action_key)))


def mark_action_in_flight(state, action_key: str) -> None:
    state[_in_flight_key(action_key)] = True


def clear_action_in_flight(state, action_key: str) -> None:
    state.pop(_in_flight_key(action_key), None)


def _in_flight_key(action_key: str) -> str:
    return f"history_edit_in_flight_{action_key}"
