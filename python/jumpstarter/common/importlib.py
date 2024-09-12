# Reference: https://docs.djangoproject.com/en/5.0/_modules/django/utils/module_loading/#import_string

import sys
from fnmatch import fnmatchcase
from importlib import import_module


def cached_import(module_path, class_name):
    # Check whether module is loaded and fully initialized.
    if not (
        (module := sys.modules.get(module_path))
        and (spec := getattr(module, "__spec__", None))
        and getattr(spec, "_initializing", False) is False
    ):
        module = import_module(module_path)
    return getattr(module, class_name)


def import_class(class_path: str, allow: list[str], unsafe: bool):
    if not unsafe:
        if not any(fnmatchcase(class_path, pattern) for pattern in allow):
            raise ImportError(f"{class_path} doesn't match any of the allowed patterns")
    try:
        module_path, class_name = class_path.rsplit(".", 1)
    except ValueError as e:
        raise ImportError(f"{class_path} doesn't look like a class path") from e
    try:
        return cached_import(module_path, class_name)
    except AttributeError as e:
        raise ImportError(f"{module_path} doesn't have specified class {class_name}") from e
