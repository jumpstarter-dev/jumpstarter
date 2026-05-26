#!/usr/bin/env python3
"""Generate markdown API reference from Kubernetes CRD YAML files."""

import glob
import os
from typing import Any

import yaml

CRD_DIR = os.path.join(
    os.path.dirname(__file__),
    "../../../../controller/deploy/operator/config/crd/bases",
)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "crds")

SKIP_EXPAND = {
    "topologySpreadConstraints",
    "resources",
    "labelSelector",
    "matchExpressions",
    "claims",
}


def flatten_properties(
    properties: dict[str, Any], prefix: str = "", depth: int = 0
) -> list[tuple[str, str, str]]:
    rows = []
    for name, prop in sorted(properties.items()):
        path = f"{prefix}{name}" if prefix else name
        typ = prop.get("type", "object")
        desc = prop.get("description", "").split("\n")[0].strip()
        default = prop.get("default")
        enum = prop.get("enum")

        type_str = typ
        if enum:
            type_str = " | ".join(f"`{e}`" for e in enum)
        if len(desc) > 120:
            desc = desc[:117] + "..."

        if default is not None:
            desc += f" (default: `{default}`)"

        rows.append((f"`{path}`", type_str, desc))

        if name in SKIP_EXPAND:
            continue

        if typ == "object" and "properties" in prop and depth < 2:
            rows.extend(
                flatten_properties(prop["properties"], f"{path}.", depth + 1)
            )
        elif typ == "array" and "items" in prop:
            items = prop["items"]
            if items.get("type") == "object" and "properties" in items and depth < 2:
                rows.extend(
                    flatten_properties(items["properties"], f"{path}[].", depth + 1)
                )

    return rows


def render_table(rows: list[tuple[str, str, str]]) -> str:
    if not rows:
        return "*No fields defined.*\n"
    lines = ["| Field | Type | Description |", "| --- | --- | --- |"]
    for field, typ, desc in rows:
        desc = desc.replace("|", r"\|")
        lines.append(f"| {field} | {typ} | {desc} |")
    return "\n".join(lines) + "\n"


def process_crd(filepath: str) -> tuple[str, str]:
    with open(filepath, encoding="utf-8") as f:
        crd = yaml.safe_load(f)

    group = crd["spec"]["group"]
    kind = crd["spec"]["names"]["kind"]
    versions = crd["spec"]["versions"]
    version = next(
        (v for v in versions if v.get("storage", False)),
        versions[0],
    )
    ver = version["name"]
    schema = version["schema"]["openAPIV3Schema"]

    sections = []
    sections.append(f"# {kind}\n")
    sections.append(f"`{group}/{ver}`\n")

    desc = schema.get("description", "")
    if desc:
        sections.append(desc.split("\n")[0] + "\n")

    spec = schema.get("properties", {}).get("spec", {})
    if spec.get("properties"):
        sections.append("## Spec\n")
        rows = flatten_properties(spec["properties"], "spec.")
        sections.append(render_table(rows))

    status = schema.get("properties", {}).get("status", {})
    if status.get("properties"):
        sections.append("## Status\n")
        rows = flatten_properties(status["properties"], "status.")
        sections.append(render_table(rows))

    return kind, "\n".join(sections)


def main(crd_dir: str = CRD_DIR, output_dir: str = OUTPUT_DIR) -> None:
    crds = sorted(glob.glob(os.path.join(crd_dir, "*.yaml")))
    if not crds:
        print(f"No CRD files found in {crd_dir}")
        return

    os.makedirs(output_dir, exist_ok=True)

    count = 0
    for crd_file in crds:
        print(f"Processing {os.path.basename(crd_file)}")
        kind, content = process_crd(crd_file)
        slug = kind.lower()
        filename = f"{slug}.md"

        with open(os.path.join(output_dir, filename), "w", encoding="utf-8") as f:
            f.write(content)

        count += 1

    print(f"Generated {count} CRD docs in {output_dir}/")


if __name__ == "__main__":
    main()
