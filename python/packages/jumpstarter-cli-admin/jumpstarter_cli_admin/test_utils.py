import json


def json_equal(a: str, b: str) -> bool:
    def _parse(s: str):
        try:
            data = json.loads(s)
        except ValueError:
            return object()
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                pass
        return data

    return _parse(a) == _parse(b)
