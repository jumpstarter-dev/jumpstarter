from jumpstarter_cli_common.completion import create_completion_command


def _get_jmp():
    from jumpstarter_cli.jmp import jmp

    return jmp


completion = create_completion_command(
    cli_group_getter=_get_jmp,
    prog_name="jmp",
    complete_var="_JMP_COMPLETE",
)
