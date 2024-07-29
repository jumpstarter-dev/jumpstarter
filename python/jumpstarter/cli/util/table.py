def make_table(columns: list[str], values: list[dict]):
    """Make a pretty table from a list of `columns` and a list of `values`, each of which is a valid `dict`."""

    # Initalize the max_lens dict with the length of the column titles
    max_lens: dict[str, int] = {}
    for name in columns:
        max_lens[name] = len(name)

    # Get the max length for each column from the values
    for v in values:
        for k in columns:
            length = len(v[k])
            if length > max_lens[k]:
                max_lens[k] = length

    # Generate a formatting string based on the max lengths
    format_str = ""
    for k in max_lens:
        format_str += f"{{:<{max_lens[k] + 3}}}"

    # Print the fromatted header
    lines: list[str] = [format_str.format(*columns)]

    # Print the formatted rows
    for v in values:
        col_vals = []
        for k in columns:
            col_vals.append(v[k])
        lines.append(format_str.format(*col_vals))

    return "\n".join(lines)
