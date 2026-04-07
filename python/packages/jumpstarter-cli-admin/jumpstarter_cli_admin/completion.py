from jumpstarter_cli_common.completion import make_completion_command


def _get_admin():
    from jumpstarter_cli_admin import admin

    return admin


completion = make_completion_command(_get_admin, "jmp-admin", "_JMP_ADMIN_COMPLETE")
