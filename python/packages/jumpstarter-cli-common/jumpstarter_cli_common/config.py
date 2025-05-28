from contextlib import suppress
from functools import partial, reduce, wraps
from pathlib import Path

import click
from pydantic import ValidationError

from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.exporter import ExporterConfigV1Alpha1
from jumpstarter.config.user import UserConfigV1Alpha1


def opt_config_inner(  # noqa: C901
    f,
    *,
    client: bool,
    exporter: bool,
    allow_missing: bool,
):
    params = {}

    def callback(ctx, param, value):
        if value is not None:
            params[param.name] = value

    option = partial(click.option, expose_value=False, callback=callback)

    options = []
    options_names = []

    if client:
        options += [
            option("--client", help="Alias of client config"),
            option("--client-config", type=click.Path(), help="Path to client config"),
        ]
        options_names += [
            "--client",
            "--client-config",
        ]

    if exporter:
        options += [
            option("--exporter", help="Alias of exporter config"),
            option("--exporter-config", type=click.Path(), help="Path of exporter config"),
        ]
        options_names += [
            "--exporter",
            "--exporter-config",
        ]

    @wraps(f)
    def wrapper(*args, **kwds):  # noqa: C901
        try:
            match len(params):
                case 0:
                    if client:
                        config = None

                        with suppress(ValidationError):
                            config = ClientConfigV1Alpha1()

                        if config is None:
                            config = UserConfigV1Alpha1.load_or_create().config.current_client

                        if config is None:
                            raise click.ClickException(
                                f"none of {', '.join(options_names)} is specified, and default config is not set"
                            )
                    else:
                        raise click.BadParameter(f"one of {', '.join(options_names)} should be specified")
                case 1:
                    try:
                        match next(iter(params.items())):
                            case ("client", alias):
                                config = ClientConfigV1Alpha1.load(alias)
                            case ("client_config", path):
                                config = ClientConfigV1Alpha1.from_file(path)
                            case ("exporter", alias):
                                config = ExporterConfigV1Alpha1.load(alias)
                            case ("exporter_config", path):
                                config = ExporterConfigV1Alpha1.load_path(Path(path))
                    except FileNotFoundError:
                        if allow_missing:
                            config = next(iter(params.items()))
                        else:
                            raise
                case _:
                    raise click.BadParameter(f"only one of {', '.join(options_names)} should be specified")
        except click.ClickException:
            raise
        except Exception as e:
            raise click.ClickException("Failed to load config: {}".format(e)) from e

        return f(*args, **kwds, config=config)

    return reduce(lambda w, opt: opt(w), options, wrapper)


def opt_config(*, client: bool = True, exporter: bool = True, allow_missing=False):
    return partial(opt_config_inner, client=client, exporter=exporter, allow_missing=allow_missing)
