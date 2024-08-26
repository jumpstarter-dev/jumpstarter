from inspect import isasyncgenfunction, iscoroutinefunction, isfunction, isgeneratorfunction
from typing import Final

MARKER_MAGIC: Final[str] = "07c9b9cc"
MARKER_DRIVERCALL: Final[str] = "marker_drivercall"
MARKER_STREAMCALL: Final[str] = "marker_streamcall"
MARKER_STREAMING_DRIVERCALL: Final[str] = "marker_streamingdrivercall"


def export(func):
    """
    Decorator for exporting method as driver call
    """
    if isasyncgenfunction(func) or isgeneratorfunction(func):
        setattr(func, MARKER_STREAMING_DRIVERCALL, MARKER_MAGIC)
    elif iscoroutinefunction(func) or isfunction(func):
        setattr(func, MARKER_DRIVERCALL, MARKER_MAGIC)
    else:
        raise ValueError(f"unsupported exported function {func}")
    return func


def exportstream(func):
    """
    Decorator for exporting method as stream
    """
    setattr(func, MARKER_STREAMCALL, MARKER_MAGIC)
    return func
