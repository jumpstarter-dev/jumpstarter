from contextlib import contextmanager
from functools import wraps


def blocking(f):
    @wraps(f)
    @contextmanager
    def wrapper(*args, **kwargs):
        with kwargs["client"].portal.wrap_async_context_manager(f(*args, **kwargs)) as res:
            yield res

    return wrapper
