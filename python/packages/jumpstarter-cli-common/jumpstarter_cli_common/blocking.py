from functools import partial, wraps

from anyio import run


def blocking(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        return run(partial(f, *args, **kwargs))

    return wrapper
