import click

opt_log_level = click.option(
       "-l", "--log-level", "log_level",
       type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
       help="Set the log level"
)

opt_kubeconfig = click.option(
       "--kubeconfig", "kubeconfig",
       type=click.File(),
       default=None,
       help="path to the kubeconfig file"
)

opt_context = click.option(
       "--context",
       "context",
       type=str,
       default=None,
       help="Kubernetes context to use"
)

opt_namespace = click.option(
       "-n",
       "--namespace",
       type=str,
       help="Kubernetes namespace to use",
       default="default"
)
