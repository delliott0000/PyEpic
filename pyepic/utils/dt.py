from datetime import datetime, timezone

__all__ = ("utc_now",)


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)
