"""Numbers and times as people read them."""
from __future__ import annotations

from datetime import datetime


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_time(seconds: int) -> str:
    if seconds <= 0:
        return "–"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"


def human_ago(when: datetime | None, now: datetime | None = None) -> str:
    if when is None:
        return "never"
    days = ((now or datetime.now()).date() - when.date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    if days < 730:
        return f"{days // 30} months ago"
    return f"{days // 365} years ago"


def human_since(when: datetime | None, now: datetime | None = None) -> str:
    """Like human_ago, but to the minute for things that happened today: "12 minutes ago"."""
    if when is None:
        return "never"
    seconds = ((now or datetime.now()) - when).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return plural(int(seconds // 60), "minute") + " ago"
    if seconds < 86400:
        return plural(int(seconds // 3600), "hour") + " ago"
    return human_ago(when, now)


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def human_duration(seconds: int) -> str:
    if seconds < 90:
        return "about a minute"
    if seconds < 3600:
        return f"about {round(seconds / 60)} minutes"
    return f"about {seconds // 3600} h {seconds % 3600 // 60:02d} min"
