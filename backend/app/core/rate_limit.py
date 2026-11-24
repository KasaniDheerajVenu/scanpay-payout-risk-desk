from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from threading import Lock
from time import monotonic


@dataclass(slots=True)
class AttemptWindow:
    expires_at: float
    count: int = 0


class LoginRateLimiter:
    def __init__(
        self, limit: int, seconds: int, max_entries: int, clock: Callable[[], float] = monotonic
    ) -> None:
        self._limit = limit
        self._seconds = seconds
        self._max_entries = max_entries
        self._clock = clock
        self._windows: OrderedDict[tuple[str, str], AttemptWindow] = OrderedDict()
        self._lock = Lock()

    def retry_after(self, username: str, peer: str) -> int | None:
        """Reserve an attempt before password verification, or return a retry delay."""
        keys = [(("account", username), self._limit), (("peer", peer), self._limit * 10)]
        with self._lock:
            now = self._clock()
            while self._windows and next(iter(self._windows.values())).expires_at <= now:
                self._windows.popitem(last=False)

            blocked = [
                self._windows[key].expires_at
                for key, limit in keys
                if key in self._windows and self._windows[key].count >= limit
            ]
            if blocked:
                return max(1, ceil(max(blocked) - now))

            new_entries = sum(key not in self._windows for key, _ in keys)
            if len(self._windows) + new_entries > self._max_entries:
                oldest = next(iter(self._windows.values()))
                return max(1, ceil(oldest.expires_at - now))

            for key, _ in keys:
                window = self._windows.setdefault(
                    key, AttemptWindow(expires_at=now + self._seconds)
                )
                window.count += 1
            return None
