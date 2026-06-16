#!/usr/bin/env python3
"""Generate golden fixtures for the Rust `Value` codec differential tests.

Runs the REAL Python codec (`jumpstarter.common.serde.encode_value`) over a set of
fixtures and records, for each:

  - ``rust_input``: the JSON-normalized form of the value — i.e. exactly what the
    Rust ``encode_value`` consumes. This is ``TypeAdapter(Any).dump_python(v,
    mode="json")``, which is also the input Python's ``json_format.ParseDict``
    sees, so Python and Rust encode the *same* logical input.
  - ``value_b64``: base64 of the serialized ``google.protobuf.Value`` produced by
    Python — the wire bytes the Rust side must reproduce / be able to read.
  - ``decoded``: Python's ``MessageToDict`` of that Value — the decode-direction
    expectation.

Run from the repo root with the project venv:

    python/.venv/bin/python \\
        rust/jumpstarter-protocol/tests/fixtures/generate_value_golden.py

The Python-specific encoding quirks (int->float, tuple->list, bytes->utf8 str) are
captured because ``rust_input`` is the normalized form while ``value_b64`` comes
from the original Python value.
"""

import base64
import json
import os
from typing import Any

from google.protobuf.json_format import MessageToDict
from pydantic import TypeAdapter

from jumpstarter.common.serde import encode_value

adapter = TypeAdapter(Any)

# (name, python value). Includes the quirk cases (tuple, bytes, ints).
FIXTURES: list[tuple[str, Any]] = [
    ("null", None),
    ("bool_true", True),
    ("bool_false", False),
    ("int_zero", 0),
    ("int_pos", 42),
    ("int_neg", -7),
    ("int_f64_boundary", 9007199254740993),  # 2^53 + 1
    ("float_pi", 3.14159),
    ("float_zero", 0.0),
    ("float_neg", -2.5),
    ("str_empty", ""),
    ("str_simple", "hello"),
    ("str_unicode", "héllo wörld 日本語 🦀"),
    ("str_special", "line1\nline2\ttab\"quote\\back"),
    ("bytes_utf8", b"raw bytes"),  # -> string_value "raw bytes"
    ("list_empty", []),
    ("list_ints", [1, 2, 3]),
    ("list_mixed", [1, "two", True, None, 3.5]),
    ("list_nested", [[1, 2], [3, [4, 5]]]),
    ("tuple_to_list", (1, 2, 3)),  # -> list_value
    ("dict_empty", {}),
    ("dict_simple", {"a": 1, "b": "two", "c": True}),
    ("dict_nested", {"outer": {"inner": [1, 2, {"deep": None}]}}),
    ("dict_unicode_keys", {"café": "value", "日本": 42}),
    (
        "complex",
        {
            "name": "device",
            "ports": [22, 80, 443],
            "meta": {"enabled": True, "ratio": 0.75},
            "tags": ["a", "b"],
            "nothing": None,
        },
    ),
]


def main() -> None:
    out = []
    for name, value in FIXTURES:
        proto = encode_value(value)
        out.append(
            {
                "name": name,
                "rust_input": adapter.dump_python(value, mode="json"),
                "value_b64": base64.b64encode(proto.SerializeToString()).decode("ascii"),
                "decoded": MessageToDict(proto),
            }
        )

    dest = os.path.join(os.path.dirname(__file__), "value_golden.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    print(f"wrote {len(out)} fixtures to {dest}")


if __name__ == "__main__":
    main()
