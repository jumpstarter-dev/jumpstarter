class DriverRegistry:
    def __init__(self):
        self._drivers = {}

    def register(self, cls: type):
        if cls.__name__ in self._drivers:
            raise ValueError(f'Driver {cls.__name__} already registered')
        self._drivers[cls.__name__] = cls
        return cls

    def get(self, name):
        return self._drivers.get(name)

    def __iter__(self):
        return iter(self._drivers.values())

    def __len__(self):
        return len(self._drivers)

    def __getitem__(self, name):
        return self.get(name)

    def __contains__(self, name):
        return name in self._drivers

    def __repr__(self):
        return f'<DriverRegistry: {list(self._drivers.keys())}>'

