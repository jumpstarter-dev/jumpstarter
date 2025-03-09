import asyncclick as click

opt_log_level = click.option(
    "-l",
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


class OutputMode(str):
    JSON = "json"
    YAML = "yaml"
    NAME = "name"


opt_output = click.option(
    "-o",
    "--output",
    type=click.Choice([OutputMode.JSON, OutputMode.YAML]),
    default=None,
    help="Output mode.",
)

opt_output_name = click.option(
    "-o",
    "--output",
    type=click.Choice([OutputMode.NAME]),
    default=None,
    help='Output mode. Use "-o name" for shorter output (resource/name).',
)
