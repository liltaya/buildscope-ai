"""Explicit legal state transitions for pipeline and stage runs"""

from enum import StrEnum


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class InvalidRunTransitionError(ValueError):
    """An illegal pipeline or stage state transition was requested."""


_ALLOWED: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.CANCELLED,
            RunStatus.BLOCKED,
        }
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.TIMED_OUT,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.TIMED_OUT: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.BLOCKED: frozenset(),
}


def assert_transition(
    current: RunStatus,
    target: RunStatus,
) -> None:
    """Raise if a transition would violate the state machine."""
    if target not in _ALLOWED[current]:
        raise InvalidRunTransitionError(f"Invalid state transition: {current} -> {target}")
