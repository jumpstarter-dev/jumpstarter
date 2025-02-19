import asyncclick as click

from jumpstarter.common.exceptions import JumpstarterException


class ClickExceptionRed(click.ClickException):
    def format_message(self) -> str:
        return click.style(self.message, fg="red")


def async_handle_exceptions(func):
    """Decorator to handle exceptions in async functions."""

    async def wrapped(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except JumpstarterException as e:
            raise ClickExceptionRed(str(e)) from None
        except click.ClickException:
            raise  # if it was already a click exception from the cli commands, just re-raise it
        except Exception:
            raise

    return wrapped


def handle_exceptions(func):
    """Decorator to handle exceptions in blocking functions."""

    def wrapped(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except JumpstarterException as e:
            raise ClickExceptionRed(str(e)) from None
        except click.ClickException:
            raise  # if it was already a click exception from the cli commands, just re-raise it
        except Exception:
            raise

    return wrapped
