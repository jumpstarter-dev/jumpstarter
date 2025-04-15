from typing import Literal, Optional

import asyncclick as click

from jumpstarter.common.pydantic import OutputMode, OutputType

__all__ = ["OutputType"]

opt_log_level = click.option(
    "--log-level",
    "log_level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
    help="Set the log level",
)

opt_kubeconfig = click.option(
    "--kubeconfig", "kubeconfig", type=click.File(), default=None, help="path to the kubeconfig file"
)

opt_context = click.option("--context", "context", type=str, default=None, help="Kubernetes context to use")

opt_namespace = click.option("-n", "--namespace", type=str, help="Kubernetes namespace to use", default="default")

opt_labels = click.option("-l", "--label", "labels", type=(str, str), multiple=True, help="Labels")


opt_output_all = click.option(
    "-o",
    "--output",
    type=click.Choice([OutputMode.JSON, OutputMode.YAML, OutputMode.NAME]),
    default=None,
    help='Output mode. Use "-o name" for shorter output (resource/name).',
)

NameOutputType = Optional[Literal["name"]]

opt_output_name_only = click.option(
    "-o",
    "--output",
    type=click.Choice([OutputMode.NAME]),
    default=None,
    help='Output mode. Use "-o name" for shorter output (resource/name).',
)

PathOutputType = Optional[Literal["path"]]

opt_output_path_only = click.option(
    "-o",
    "--output",
    type=click.Choice([OutputMode.PATH]),
    default=None,
    help='Output mode. Use "-o path" for shorter output (file/path).',
)

opt_nointeractive = click.option(
    "--nointeractive", is_flag=True, default=False, help="Disable interactive prompts (for use in scripts)."
)


def opt_output_auto(cls):
    choices = []
    if hasattr(cls, "dump_json"):
        choices.append(OutputMode.JSON)
    if hasattr(cls, "dump_yaml"):
        choices.append(OutputMode.YAML)
    if hasattr(cls, "dump_name"):
        choices.append(OutputMode.NAME)
    if hasattr(cls, "dump_path"):
        choices.append(OutputMode.PATH)

    if OutputMode.PATH in choices:
        help = 'Output mode. Use "-o path" for shorter output (file/path).'
    elif OutputMode.NAME in choices:
        help = 'Output mode. Use "-o name" for shorter output (resource/name).'
    else:
        help = "Output mode."

    return click.option(
        "-o",
        "--output",
        type=click.Choice(choices),
        default=None,
        help=help,
    )
