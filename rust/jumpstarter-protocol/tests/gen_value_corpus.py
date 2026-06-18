#!/usr/bin/env python3
"""Generate the differential ``MessageToJson`` golden corpus for the Rust codec.

This is the text-level companion to ``fixtures/generate_value_golden.py`` (which
records the raw wire bytes). Here we record what Python's
``google.protobuf.json_format.MessageToJson`` emits for the ``Value`` produced by
the real Python codec (``jumpstarter.common.serde.encode_value``). The Rust test
parses that text with ``serde_json`` and compares it structurally (numbers as
f64) against the proto the Rust ``encode_value`` produces, proving the two codecs
are byte-exact at the ``google.protobuf.Value`` level.

For each fixture we record:

  - ``name``       — a stable identifier.
  - ``input_json`` — the JSON form a Rust test feeds to ``encode_value``. For
    inputs that aren't directly JSON-representable (tuple, bytes), this is the
    normalized JSON form Rust consumes (tuple -> array, bytes -> the utf8
    string). ``null`` for the non-finite cases, which Rust cannot feed through
    ``encode_value`` (serde_json can't parse ``NaN``) and which are therefore
    *decode-only* fixtures.
  - ``proto_json`` — Python ``MessageToJson(encode_value(v), indent=0)``: the
    text the Rust-produced proto must match (parsed, compared as f64).
  - ``decode_only`` — true for non-finite inputs (NaN/Inf). The encode-direction
    assertion is skipped for these (no serde_json input exists); only the
    decode-direction null-mapping is exercised, in Rust.
  - ``note``       — human context for the load-bearing quirks.

Run from the repo root with the project venv::

    python/.venv/bin/python \\
        rust/jumpstarter-protocol/tests/gen_value_corpus.py
"""

import json
import math
import os
from typing import Any

from google.protobuf.json_format import MessageToJson
from pydantic import TypeAdapter

from jumpstarter.common.serde import encode_value

adapter = TypeAdapter(Any)

NAN = float("nan")
INF = float("inf")
NEG_INF = float("-inf")

# (name, python value, decode_only, note)
#
# ``decode_only`` marks inputs that have no serde_json representation (NaN/Inf):
# Rust can never feed them to encode_value, so the corpus only asserts the
# decode-direction behavior for them. For every other entry the Rust test both
# (a) encodes input_json and compares to proto_json, and (b) round-trips.
FIXTURES: list[tuple[str, Any, bool, str]] = [
    # --- scalars -----------------------------------------------------------
    ("null", None, False, ""),
    ("bool_true", True, False, ""),
    ("bool_false", False, False, ""),
    ("str_empty", "", False, ""),
    ("str_unicode", "unicode héllo 日本語 🦀", False, "non-ascii + multibyte + emoji"),
    ("str_special", 'line1\nline2\ttab"quote\\back', False, "json escapes"),
    ("int_zero", 0, False, "int -> double"),
    ("int_pos", 42, False, "int -> double"),
    ("int_neg", -7, False, "int -> double"),
    ("float_half", 3.5, False, ""),
    ("float_zero", 0.0, False, ""),
    ("float_neg", -2.5, False, ""),
    # --- large-int precision boundary -------------------------------------
    ("int_2_53", 2**53, False, "9007199254740992: exactly representable as f64"),
    (
        "int_2_53_plus1",
        2**53 + 1,
        False,
        "9007199254740993 rounds to 9007199254740992.0 in BOTH Python and Rust",
    ),
    ("int_neg_2_53", -(2**53), False, "negative f64 integer boundary"),
    # --- small / extreme floats -------------------------------------------
    ("float_tiny", 1e-300, False, "subnormal-ish small float, MessageToJson -> 1e-300"),
    ("float_huge", 1e300, False, "MessageToJson emits 1e+300"),
    ("float_eps", 2.220446049250313e-16, False, "machine epsilon"),
    # --- containers --------------------------------------------------------
    ("list_empty", [], False, ""),
    ("dict_empty", {}, False, ""),
    ("list_ints", [1, 2, 3], False, ""),
    ("list_mixed", [1, "two", True, None, 3.5], False, "mixed-type list"),
    ("list_nested", [[1, 2], [3, [4, 5]]], False, ""),
    ("dict_simple", {"a": 1, "b": "two", "c": True}, False, ""),
    ("dict_nested", {"outer": {"inner": [1, 2, {"deep": None}]}}, False, ""),
    ("dict_unicode_keys", {"café": "value", "日本": 42}, False, "non-ascii keys"),
    (
        "complex",
        {
            "name": "device",
            "ports": [22, 80, 443],
            "meta": {"enabled": True, "ratio": 0.75},
            "tags": ["a", "b"],
            "nothing": None,
        },
        False,
        "representative driver-call payload",
    ),
    # --- the quirks --------------------------------------------------------
    (
        "tuple_to_list",
        (1, 2, 3),
        False,
        "Python tuple -> list_value; input_json is [1,2,3]",
    ),
    (
        "bytes_utf8",
        b"hi",
        False,
        "pydantic ser_json_bytes='utf8' default: bytes -> the UTF-8 STRING 'hi', "
        "NOT base64; input_json is \"hi\"",
    ),
    (
        "bytes_utf8_longer",
        b"raw bytes",
        False,
        "bytes -> utf8 string 'raw bytes' (not base64)",
    ),
    # --- non-finite floats (DECODE-ONLY) ----------------------------------
    # pydantic's mode='json' dump turns non-finite floats into None, so the
    # Python codec encodes NaN/Inf as NULL (NOT a 'NaN'/'Infinity' token).
    # MessageToJson therefore emits "null". Rust can't parse NaN from JSON, so
    # these only check the decode side (NumberValue(NaN) -> null) in the test.
    ("nan", NAN, True, "pydantic mode=json: NaN -> None -> Value null"),
    ("inf", INF, True, "pydantic mode=json: +Inf -> None -> Value null"),
    ("neg_inf", NEG_INF, True, "pydantic mode=json: -Inf -> None -> Value null"),
]


def main() -> None:
    out = []
    for name, value, decode_only, note in FIXTURES:
        proto = encode_value(value)
        # indent=0 keeps it on its own lines but still valid JSON; we parse it
        # in Rust so the exact whitespace does not matter.
        proto_json_text = MessageToJson(proto, indent=0)

        if decode_only:
            input_json: Any = None
        else:
            # The normalized JSON form Rust's encode_value consumes. This is what
            # json_format.ParseDict also sees, so Python and Rust encode the same
            # logical input. (tuple -> list, bytes -> utf8 string, int stays int
            # in the JSON text but both sides collapse it to a double.)
            input_json = adapter.dump_python(value, mode="json")

        # Sanity: the non-finite cases really do collapse to a null Value text.
        if decode_only:
            assert json.loads(proto_json_text) is None, (
                f"{name}: expected non-finite to encode as null, "
                f"got {proto_json_text!r}"
            )

        out.append(
            {
                "name": name,
                "input_json": input_json,
                "proto_json": proto_json_text,
                "decode_only": decode_only,
                "note": note,
            }
        )

    dest = os.path.join(os.path.dirname(__file__), "value_corpus.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"wrote {len(out)} corpus entries to {dest}")

    # Echo the load-bearing quirk outputs so a reviewer can eyeball them.
    by_name = {f[0]: f[1] for f in FIXTURES}
    print("\nload-bearing quirk outputs (Python source of truth):")
    for name in ("bytes_utf8", "nan", "inf", "int_2_53_plus1", "float_huge"):
        proto = encode_value(by_name[name])
        print(f"  {name:16} -> {MessageToJson(proto, indent=0)!r}")
    # math import kept meaningful: assert our NAN really is non-finite.
    assert math.isnan(NAN) and math.isinf(INF)


if __name__ == "__main__":
    main()
