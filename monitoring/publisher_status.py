from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

PublisherState = Literal["disabled", "running", "ok", "error"]


@dataclass
class PublisherStatus:
    name: str
    state: PublisherState = "running"
    enabled: bool = True
    detail: dict[str, Any] = field(default_factory=dict)

    last_attempt_ts: float | None = None
    last_success_ts: float | None = None
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        now = time.time()
        d["age_secs"] = None if self.last_success_ts is None else max(0.0, now - float(self.last_success_ts))
        return d


_lock = threading.Lock()
_statuses: dict[str, PublisherStatus] = {}


def set_publisher_status(
    name: str,
    *,
    state: PublisherState | None = None,
    enabled: bool | None = None,
    detail: dict[str, Any] | None = None,
    last_attempt_ts: float | None = None,
    last_success_ts: float | None = None,
    last_error: str | None = None,
) -> None:
    """
    In-process diagnostics channel for background publishers.
    Safe to call frequently; updates are last-write-wins.
    """
    with _lock:
        st = _statuses.get(name) or PublisherStatus(name=name)
        if state is not None:
            st.state = state
        if enabled is not None:
            st.enabled = enabled
        if detail is not None:
            st.detail = detail
        if last_attempt_ts is not None:
            st.last_attempt_ts = last_attempt_ts
        if last_success_ts is not None:
            st.last_success_ts = last_success_ts
        if last_error is not None:
            st.last_error = last_error
        _statuses[name] = st


def get_publisher_statuses() -> dict[str, dict[str, Any]]:
    with _lock:
        return {k: v.as_dict() for k, v in sorted(_statuses.items(), key=lambda kv: kv[0])}

