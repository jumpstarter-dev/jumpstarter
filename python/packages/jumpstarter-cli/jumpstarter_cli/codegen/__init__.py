"""jmp codegen — generate typed device wrappers from ExporterClass definitions.

Two-stage pipeline:
  Stage 1 (stubs):  standard protoc generates language-specific message/service stubs
  Stage 2 (codegen): jmp codegen composes stubs into ExporterClass-typed device wrappers
"""

from __future__ import annotations

import asyncio
import os

import click
from jumpstarter_cli_common.alias import AliasedGroup

from .engine import (
    CodegenContext,
    available_languages,
    get_language_generator,
    resolve_exporter_class_from_cluster,
    resolve_exporter_class_from_file,
    write_generated_files,
)

# Common options shared across subcommands

_language_option = click.option(
    "--language", "-l",
    required=True,
    help="Target language (python, java, typescript, rust).",
)

_output_option = click.option(
    "--output", "-o",
    type=click.Path(),
    required=True,
    help="Output directory for generated files.",
)

_exporter_class_option = click.option(
    "--exporter-class",
    default=None,
    help="ExporterClass name (resolved from the cluster).",
)

_exporter_class_file_option = click.option(
    "--exporter-class-file",
    type=click.Path(exists=True),
    default=None,
    help="Path to an ExporterClass YAML file (offline mode).",
)

_test_fixtures_option = click.option(
    "--test-fixtures",
    is_flag=True,
    default=False,
    help="Generate test framework fixtures alongside client code.",
)

_driver_interface_dir_option = click.option(
    "--driver-interface-dir",
    type=click.Path(exists=True),
    default=None,
    help="Directory containing DriverInterface YAML files for offline resolution.",
)

_proto_search_path_option = click.option(
    "--proto-search-path", "-I",
    multiple=True,
    help="Additional paths to search for .proto files.",
)

_package_name_option = click.option(
    "--package-name",
    default=None,
    help="Override package name for the generated output.",
)


def _resolve_exporter_class(
    exporter_class: str | None,
    exporter_class_file: str | None,
    driver_interface_dir: str | None,
    proto_search_path: tuple[str, ...],
):
    """Resolve an ExporterClass from either a file or the cluster."""
    if exporter_class_file:
        return resolve_exporter_class_from_file(
            yaml_path=exporter_class_file,
            proto_search_paths=list(proto_search_path) if proto_search_path else None,
            driver_interface_dir=driver_interface_dir,
        )
    elif exporter_class:
        return asyncio.get_event_loop().run_until_complete(
            resolve_exporter_class_from_cluster(exporter_class)
        )
    else:
        raise click.ClickException(
            "Specify --exporter-class (cluster) or --exporter-class-file (offline)."
        )


@click.group(cls=AliasedGroup, invoke_without_command=True)
@_language_option
@_output_option
@_exporter_class_option
@_exporter_class_file_option
@_test_fixtures_option
@_driver_interface_dir_option
@_proto_search_path_option
@_package_name_option
@click.pass_context
def codegen(
    ctx,
    language: str,
    output: str,
    exporter_class: str | None,
    exporter_class_file: str | None,
    test_fixtures: bool,
    driver_interface_dir: str | None,
    proto_search_path: tuple[str, ...],
    package_name: str | None,
):
    """Generate typed device wrappers from ExporterClass definitions.

    \b
    All-in-one (stubs + wrapper + test fixtures):
      jmp codegen -l java --exporter-class-file dev-board.yaml -o src/gen/

    \b
    Use subcommands for Stage 1 or Stage 2 only:
      jmp codegen stubs -l java --exporter-class-file dev-board.yaml -o src/gen/
      jmp codegen exporter-class -l java --exporter-class-file dev-board.yaml -o src/gen/
    """
    if ctx.invoked_subcommand is not None:
        return

    # All-in-one mode
    ec_spec = _resolve_exporter_class(
        exporter_class, exporter_class_file, driver_interface_dir, proto_search_path,
    )

    generator = get_language_generator(language)
    codegen_ctx = CodegenContext(
        exporter_class=ec_spec,
        language=language,
        output_dir=os.path.abspath(output),
        generate_test_fixtures=test_fixtures,
        package_name=package_name,
    )

    all_files = generator.generate_all(codegen_ctx)
    written = write_generated_files(all_files, codegen_ctx.output_dir)

    for path in written:
        click.echo(f"  Generated {path}")
    click.secho(
        f"\nGenerated {len(written)} file(s) for {language} "
        f"from ExporterClass '{ec_spec.name}'.",
        fg="green",
    )


@codegen.command("stubs")
@_language_option
@_output_option
@_exporter_class_option
@_exporter_class_file_option
@_driver_interface_dir_option
@_proto_search_path_option
@click.option(
    "--interfaces",
    default=None,
    help="Comma-separated list of interface names to generate stubs for.",
)
def stubs(
    language: str,
    output: str,
    exporter_class: str | None,
    exporter_class_file: str | None,
    driver_interface_dir: str | None,
    proto_search_path: tuple[str, ...],
    interfaces: str | None,
):
    """Generate interface stubs only (Stage 1 — standard protoc).

    \b
    From an ExporterClass:
      jmp codegen stubs -l java --exporter-class-file dev-board.yaml -o src/gen/

    \b
    For specific interfaces:
      jmp codegen stubs -l java --interfaces power-v1,serial-v1 -o src/gen/
    """
    ec_spec = _resolve_exporter_class(
        exporter_class, exporter_class_file, driver_interface_dir, proto_search_path,
    )

    generator = get_language_generator(language)
    codegen_ctx = CodegenContext(
        exporter_class=ec_spec,
        language=language,
        output_dir=os.path.abspath(output),
        generate_test_fixtures=False,
    )

    # Generate only per-interface client stubs
    all_files: dict[str, str] = {}
    target_interfaces = ec_spec.interfaces
    if interfaces:
        names = {n.strip() for n in interfaces.split(",")}
        target_interfaces = [i for i in ec_spec.interfaces if i.name in names]
        if not target_interfaces:
            raise click.ClickException(
                f"No matching interfaces found. Available: "
                f"{', '.join(i.name for i in ec_spec.interfaces)}"
            )

    for interface in target_interfaces:
        all_files.update(generator.generate_interface_client(codegen_ctx, interface))

    written = write_generated_files(all_files, codegen_ctx.output_dir)
    for path in written:
        click.echo(f"  Generated {path}")
    click.secho(f"\nGenerated {len(written)} stub file(s) for {language}.", fg="green")


@codegen.command("exporter-class")
@_language_option
@_output_option
@_exporter_class_option
@_exporter_class_file_option
@_test_fixtures_option
@_driver_interface_dir_option
@_proto_search_path_option
@_package_name_option
def exporter_class(
    language: str,
    output: str,
    exporter_class: str | None,
    exporter_class_file: str | None,
    test_fixtures: bool,
    driver_interface_dir: str | None,
    proto_search_path: tuple[str, ...],
    package_name: str | None,
):
    """Generate ExporterClass wrapper only (Stage 2 — Jumpstarter-specific).

    \b
    Example:
      jmp codegen exporter-class -l java --exporter-class-file dev-board.yaml -o src/gen/
    """
    ec_spec = _resolve_exporter_class(
        exporter_class, exporter_class_file, driver_interface_dir, proto_search_path,
    )

    generator = get_language_generator(language)
    codegen_ctx = CodegenContext(
        exporter_class=ec_spec,
        language=language,
        output_dir=os.path.abspath(output),
        generate_test_fixtures=test_fixtures,
        package_name=package_name,
    )

    # Generate wrapper + optionally test fixtures + package metadata
    all_files: dict[str, str] = {}
    all_files.update(generator.generate_device_wrapper(codegen_ctx))
    if test_fixtures:
        all_files.update(generator.generate_test_fixture(codegen_ctx))
    all_files.update(generator.generate_package_metadata(codegen_ctx))

    written = write_generated_files(all_files, codegen_ctx.output_dir)
    for path in written:
        click.echo(f"  Generated {path}")
    click.secho(
        f"\nGenerated {len(written)} wrapper file(s) for {language} "
        f"from ExporterClass '{ec_spec.name}'.",
        fg="green",
    )
