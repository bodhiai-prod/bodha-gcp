"""Hard shared budget for provider work after a paper draft is received."""
from __future__ import annotations

import contextvars
import time
from typing import Callable, Awaitable
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
    persist: Callable[[], Awaitable[None]] | None = None
    elapsed_before: float = 0.0
    loop: object = None

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
            "elapsed_seconds": round(max(0.0, time.monotonic() - self.started + self.elapsed_before), 2),
            "deadline_exceeded": time.monotonic() >= self.deadline,
            "exhausted_reason": self.exhausted_reason,
        }


_budget: contextvars.ContextVar[ReviewBudget | None] = contextvars.ContextVar("paper_review_budget", default=None)


def begin(*, seconds: float = 180.0, call_limit: int = 12) -> ReviewBudget:
    import asyncio
    now = time.monotonic()
    budget = ReviewBudget(now, now + seconds, call_limit)
    try:
        budget.loop = asyncio.get_running_loop()
    except RuntimeError:
        pass
    _budget.set(budget)
    return budget


def current() -> ReviewBudget | None:
    return _budget.get()


def reserve_provider_attempt() -> None:
    budget = current()
    if budget is not None:
        budget.reserve()


async def reserve_persisted_provider_attempt() -> None:
    budget = current()
    if budget is not None:
        budget.reserve()
        if budget.persist:
            await budget.persist()


def clear() -> None:
    _budget.set(None)


def reserve_thread_provider_retry() -> None:
    """Bill nested image retries on the owning event loop before dispatch."""
    import asyncio
    budget = current()
    if budget is None:
        return
    if budget.persist and budget.loop:
        future = asyncio.run_coroutine_threadsafe(reserve_persisted_provider_attempt(), budget.loop)
        future.result(timeout=max(1.0, budget.remaining_seconds()))
    else:
        budget.reserve()
