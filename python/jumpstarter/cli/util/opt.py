import click

opt_log_level = click.option(
       "-l", "--log-level", "log_level",
       type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
       help="Log level"
)
