from asyncio import run  # noqa: TID251
from functools import wraps


def blocking(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        return run(f(*args, **kwargs))

    return wrapper
