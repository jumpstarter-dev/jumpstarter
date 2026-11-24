#!/usr/bin/env python3
"""Generate the exporter lifecycle state-machine diagram for the docs."""

import os

from jumpstarter.exporter.fsm import Available, Starting
from jumpstarter.fsm_mermaid import mermaid

OUTPUT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "introduction", "exporter-fsm-diagram.md"
)

# The default layout tangles the arrows of the nested lease machine; ELK routes them at right angles.
ELK_LAYOUT = '%%{init: {"layout": "elk"}}%%\n'


def main(output_path: str = OUTPUT_PATH) -> str:
    """Render the production lifecycle states as a MyST mermaid include file."""
    # Controller mode starts at Starting; standalone mode has no controller and starts at Available.
    content = f"```{{mermaid}}\n{ELK_LAYOUT}{mermaid(Starting, Available)}\n```\n"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Generated {output_path}")
    return content


if __name__ == "__main__":
    main()
