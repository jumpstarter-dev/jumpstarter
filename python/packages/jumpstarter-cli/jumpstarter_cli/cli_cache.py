import json

import click


def serialize_click_group(group):
    def _serialize(cmd):
        result = {
            "name": cmd.name,
            "help": cmd.help or "",
        }

        if isinstance(cmd, click.Group):
            result["commands"] = {}
            for name in cmd.list_commands(None):
                subcmd = cmd.get_command(None, name)
                if subcmd:
                    result["commands"][name] = _serialize(subcmd)

        result["params"] = []
        for param in cmd.params:
            p = {"name": param.name}
            if isinstance(param, click.Option):
                p["opts"] = param.opts
                p["secondary_opts"] = param.secondary_opts
                p["is_flag"] = param.is_flag
                p["multiple"] = param.multiple
                if isinstance(param.type, click.Choice):
                    p["choices"] = list(param.type.choices)
            elif isinstance(param, click.Argument):
                p["nargs"] = param.nargs
            result["params"].append(p)

        return result

    return json.dumps(_serialize(group))


def deserialize_click_group(data):
    tree = json.loads(data)

    def _build(node):
        params = []
        for p in node.get("params", []):
            if "opts" in p:
                kwargs = {}
                if "choices" in p:
                    kwargs["type"] = click.Choice(p["choices"])
                if p.get("is_flag"):
                    kwargs["is_flag"] = True
                if p.get("multiple"):
                    kwargs["multiple"] = True
                params.append(click.Option(p["opts"] + p.get("secondary_opts", []), **kwargs))
            elif "nargs" in p:
                params.append(click.Argument([p["name"]], nargs=p["nargs"]))

        if "commands" in node:
            group = click.Group(name=node["name"], help=node.get("help"))
            group.params = params
            for name, subcmd_data in node["commands"].items():
                group.add_command(_build(subcmd_data), name)
            return group

        cmd = click.Command(name=node["name"], help=node.get("help"), callback=lambda: None)
        cmd.params = params
        return cmd

    return _build(tree)
