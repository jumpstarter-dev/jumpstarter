"""Drop-in ``yaml.safe_load`` / ``yaml.safe_dump`` replacement backed by the Rust core.

Config YAML parsing/serialization lives in the Rust core (``jumpstarter_core.parse_yaml`` /
``dump_yaml``), so the Python config layer carries no pyyaml dependency. ``safe_dump`` matches
``yaml.safe_dump(..., sort_keys=False)`` byte-for-byte (key order preserved).
"""

import json

import jumpstarter_core as jc


def safe_load(source):
    """Parse a YAML string or text file object into a Python object."""
    text = source.read() if hasattr(source, "read") else source
    return json.loads(jc.parse_yaml(text))


def safe_dump(data, stream=None, sort_keys=False, **_kwargs):
    """Serialize ``data`` to YAML (preserving key order). Writes to ``stream`` if given,
    otherwise returns the string. ``sort_keys`` is accepted for compatibility but the Rust
    serializer always preserves insertion order (the historic ``sort_keys=False`` behavior)."""
    text = jc.dump_yaml(json.dumps(data))
    if stream is not None:
        stream.write(text)
        return None
    return text
