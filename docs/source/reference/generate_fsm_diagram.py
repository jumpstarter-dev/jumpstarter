#!/usr/bin/env python3
"""Generate the exporter lifecycle state-machine diagram for the docs."""

import os

from jumpstarter.exporter.fsm import Starting
from jumpstarter.fsm_mermaid import mermaid

OUTPUT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "introduction", "exporter-fsm-diagram.md"
)


def main(output_path: str = OUTPUT_PATH) -> str:
    """Render the production lifecycle states as a MyST mermaid include file."""
    content = f"```{{mermaid}}\n{mermaid(Starting)}\n```\n"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Generated {output_path}")
    return content


if __name__ == "__main__":
    main()
