from asyncio import run
from functools import wraps


def blocking(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        return run(f(*args, **kwargs))

    return wrapper
