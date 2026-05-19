#!/usr/bin/env python3
"""Generate markdown API reference from Kubernetes CRD YAML files."""

import glob
import os

import yaml

CRD_DIR = os.path.join(
    os.path.dirname(__file__),
    "../../../../controller/deploy/operator/config/crd/bases",
)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "kubernetes-api-extensions")


def flatten_properties(properties, prefix="", depth=0):
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
        if default is not None:
            type_str += f" (default: `{default}`)"

        if len(desc) > 120:
            desc = desc[:117] + "..."

        rows.append((f"`{path}`", type_str, desc))

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


def render_table(rows):
    if not rows:
        return "*No fields defined.*\n"
    lines = ["| Field | Type | Description |", "| --- | --- | --- |"]
    for field, typ, desc in rows:
        lines.append(f"| {field} | {typ} | {desc} |")
    return "\n".join(lines) + "\n"


def process_crd(filepath):
    with open(filepath) as f:
        crd = yaml.safe_load(f)

    group = crd["spec"]["group"]
    kind = crd["spec"]["names"]["kind"]
    version = crd["spec"]["versions"][0]
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


def main():
    crds = sorted(glob.glob(os.path.join(CRD_DIR, "*.yaml")))
    if not crds:
        print(f"No CRD files found in {CRD_DIR}")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    toctree_entries = []
    index_entries = []

    for crd_file in crds:
        print(f"Processing {os.path.basename(crd_file)}")
        kind, content = process_crd(crd_file)
        slug = kind.lower()
        filename = f"{slug}.md"

        with open(os.path.join(OUTPUT_DIR, filename), "w") as f:
            f.write(content)

        toctree_entries.append(filename)
        index_entries.append(f"- [{kind}]({filename})")

    index = "# Kubernetes API Extensions\n\n"
    for entry in index_entries:
        index += entry + "\n"
    index += "\n```{toctree}\n:maxdepth: 1\n:hidden:\n\n"
    for entry in toctree_entries:
        index += entry + "\n"
    index += "```\n"

    with open(os.path.join(OUTPUT_DIR, "index.md"), "w") as f:
        f.write(index)

    print(f"Generated {len(toctree_entries)} CRD docs in {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
