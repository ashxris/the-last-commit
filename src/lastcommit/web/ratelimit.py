"""Daily usage limits, persisted in SQLite so restarts don't reset them.

No sign-in means the honest per-user key is the client IP.  Two counters:
per-IP reports/day (default 5) and a global daily budget (default 100)
that caps total API spend regardless of how many IPs show up.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class RateLimitExceeded(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class RateLimiter:
    def __init__(self, db_path: Path, per_ip_daily: int = 5, global_daily: int = 100):
        self.per_ip_daily = per_ip_daily
        self.global_daily = global_daily
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS usage ("
            " key TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL,"
            " PRIMARY KEY (key, day))"
        )
        self.db.commit()
        self._lock = threading.Lock()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _count(self, key: str, day: str) -> int:
        row = self.db.execute("SELECT count FROM usage WHERE key = ? AND day = ?", (key, day)).fetchone()
        return row[0] if row else 0

    def check(self, ip: str) -> tuple[int, int]:
        """(per-IP remaining, global remaining) without consuming anything."""
        day = self._today()
        with self._lock:
            ip_left = self.per_ip_daily - self._count(f"ip:{ip}", day)
            global_left = self.global_daily - self._count("__global__", day)
        return max(ip_left, 0), max(global_left, 0)

    def consume(self, ip: str) -> tuple[int, int]:
        """Atomically consume one report slot for this IP and the global
        budget.  Raises RateLimitExceeded if either is exhausted."""
        day = self._today()
        with self._lock:
            ip_key, global_key = f"ip:{ip}", "__global__"
            ip_count = self._count(ip_key, day)
            global_count = self._count(global_key, day)
            if ip_count >= self.per_ip_daily:
                raise RateLimitExceeded(
                    f"You've used all {self.per_ip_daily} exhumations for today. "
                    "The spirits rest until midnight UTC."
                )
            if global_count >= self.global_daily:
                raise RateLimitExceeded(
                    "The cemetery is full for today — the global daily limit of "
                    f"{self.global_daily} reports has been reached. Come back tomorrow."
                )
            for key, count in ((ip_key, ip_count), (global_key, global_count)):
                self.db.execute(
                    "INSERT INTO usage(key, day, count) VALUES (?, ?, ?) "
                    "ON CONFLICT(key, day) DO UPDATE SET count = count + 1",
                    (key, day, count + 1),
                )
            self.db.commit()
            return self.per_ip_daily - ip_count - 1, self.global_daily - global_count - 1
