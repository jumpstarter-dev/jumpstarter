
from . import registry

_registry = registry.Registry()

def register(cls):
    _registry.register(cls)

def get(name):
    return _registry.get(name)

def __iter__():
    return iter(_registry)

def __len__():
    return len(_registry)

def __getitem__(name):
    return _registry.get(name)

def __contains__(name):
    return name in _registry
