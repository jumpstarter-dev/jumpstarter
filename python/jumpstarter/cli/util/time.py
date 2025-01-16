from datetime import datetime, timezone


def time_since(t_str: str):
    # Format the elapsed time in a readable way
    t = datetime.strptime(t_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    elapsed = now - t

    # Format the elapsed time in a readable way
    if elapsed.total_seconds() < 60:
        return f"{int(elapsed.total_seconds())}s"
    elif elapsed.total_seconds() < 3600:
        minutes = int(elapsed.total_seconds() // 60)
        seconds = int(elapsed.total_seconds() % 60)
        return f"{minutes}m{seconds}s" if seconds > 0 else f"{minutes}m"
    elif elapsed.total_seconds() < 86400:
        hours = int(elapsed.total_seconds() // 3600)
        minutes = int((elapsed.total_seconds() % 3600) // 60)
        return f"{hours}h{minutes}m" if minutes > 0 and hours < 2 else f"{hours}h"
    elif elapsed.total_seconds() < 2592000:
        days = elapsed.days
        hours = int((elapsed.total_seconds() % 86400) // 3600)
        return f"{days}d{hours}h" if hours > 0 else f"{days}d"
    elif elapsed.total_seconds() < 31536000:
        months = int(elapsed.days / 30)
        days = elapsed.days % 30
        return f"{months}mo{days}d" if days > 0 else f"{months}mo"
    else:
        years = int(elapsed.days / 365)
        months = int((elapsed.days % 365) / 30)
        return f"{years}y{months}mo" if months > 0 else f"{years}y"
