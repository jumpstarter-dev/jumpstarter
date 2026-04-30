from jumpstarter_cli_common.completion import make_completion_command


def _get_jmp():
    from jumpstarter_cli.jmp import jmp

    return jmp


completion = make_completion_command(_get_jmp, "jmp", "_JMP_COMPLETE")
