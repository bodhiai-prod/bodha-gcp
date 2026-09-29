"""Hard shared budget for provider work after a paper draft is received."""
from __future__ import annotations

import contextvars
import time
from dataclasses import dataclass


class ReviewBudgetExceeded(RuntimeError):
    """A post-draft provider call would exceed the review budget."""


@dataclass
class ReviewBudget:
    started: float
    deadline: float
    call_limit: int
    calls_used: int = 0
    exhausted_reason: str | None = None

    def reserve(self) -> None:
        if time.monotonic() >= self.deadline:
            self.exhausted_reason = (
                f"The {round(self.deadline - self.started)}-second final-quality budget expired."
            )
            raise ReviewBudgetExceeded(self.exhausted_reason)
        if self.calls_used >= self.call_limit:
            self.exhausted_reason = f"The {self.call_limit}-call final-quality budget was used."
            raise ReviewBudgetExceeded(self.exhausted_reason)
        self.calls_used += 1

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def snapshot(self) -> dict:
        return {
            "provider_calls_used": self.calls_used,
            "provider_calls_limit": self.call_limit,
            "elapsed_seconds": round(max(0.0, time.monotonic() - self.started), 2),
            "deadline_exceeded": time.monotonic() >= self.deadline,
            "exhausted_reason": self.exhausted_reason,
        }


_budget: contextvars.ContextVar[ReviewBudget | None] = contextvars.ContextVar("paper_review_budget", default=None)


def begin(*, seconds: float = 180.0, call_limit: int = 12) -> ReviewBudget:
    now = time.monotonic()
    budget = ReviewBudget(now, now + seconds, call_limit)
    _budget.set(budget)
    return budget


def current() -> ReviewBudget | None:
    return _budget.get()


def reserve_provider_attempt() -> None:
    budget = current()
    if budget is not None:
        budget.reserve()
