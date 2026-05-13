from functools import wraps

import anyio


def blocking(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        async def _run():
            return await f(*args, **kwargs)
        return anyio.run(_run)

    return wrapper
