"""Main Jumpstarter CLI"""
import click
import sys


from .version import version


@click.group(context_settings={"help_option_names": ['-h', '--help']}, no_args_is_help=True)
def main():
    pass


main.add_command(version)


if __name__ == "__main__":
    main()
