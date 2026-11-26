"""Bash completion for the ``j`` command inside ``jmp shell``.

``j`` builds its command tree from the drivers of the exporter it is
connected to. ``jmp shell`` already holds a connected client before the shell
starts, so it renders that tree once into bash lookup tables and hands them to
the shell together with a completion function written in plain bash. A TAB
press then starts no process and does not contact the exporter.

The tables are keyed by the command path, "j power on":

- ``_j_sub``: visible subcommands of a group; ``_j_hid``: hidden ones (aliases)
- ``_j_opt``: visible option names of a command; ``_j_pos``: its first argument
- ``_j_val``: "<path> <option>" for each option that takes a value
- ``_j_rep``: "<path> <option>" for each option that can be given more than once

A value spec is ``choice:`` followed by the choices one per line, ``dir``, or
``file``, which is also used for free text, where a filename is the most useful guess. When nothing
matches, the ``-o default`` registration falls back to filename completion.
Completion callbacks a driver computes at runtime (``shell_complete=``) are not
available to a static table and are left out. A subcommand whose CLI fails to
build is left out too, so the other drivers still complete.

The script needs bash 4.2 or later, for ``declare -g``, ``mapfile`` and
``compopt``. On older versions, such as the bash 3.2 macOS ships, it returns
before defining anything.
"""

import logging
import shlex

import click

logger = logging.getLogger(__name__)

_BASH_FUNCTIONS = r"""
# bash completion for j, the Jumpstarter driver CLI

# Needs bash 4.2 for declare -g, mapfile and compopt. Sourced: return, not exit.
(( BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 2) )) || return 0

# Not compgen -W: it expands the word list, so a value such as $(cmd) would run.
# $3: words to leave out, as " a b "
_j_match() {
    local -a words
    local w
    read -ra words <<< "$1"
    for w in "${words[@]}"; do
        [[ $w == "$2"* && ${3-} != *" $w "* ]] && COMPREPLY+=("$w")
    done
}

# Choices are one per line and may hold spaces or shell characters: offer them
# quoted, so they reach the command as one word and unexpanded.
_j_choice() {
    local -a words
    local w q
    mapfile -t words <<< "$1"
    for w in "${words[@]}"; do
        [[ -n $w && $w == "$2"* ]] || continue
        printf -v q '%q' "$w"
        COMPREPLY+=("$q")
    done
}

_j_value() {
    case $1 in
        choice:*) _j_choice "${1#choice:}" "$2" ;;
        dir)
            compopt -o filenames 2>/dev/null
            mapfile -t -O "${#COMPREPLY[@]}" COMPREPLY < <(compgen -d -- "$2")
            ;;
        *)
            compopt -o filenames 2>/dev/null
            mapfile -t -O "${#COMPREPLY[@]}" COMPREPLY < <(compgen -f -- "$2")
            ;;
    esac
}

_j_complete() {
    COMPREPLY=()
    local cur=${COMP_WORDS[COMP_CWORD]} prev=${COMP_WORDS[COMP_CWORD-1]} key=j used=" " w i
    for ((i = 1; i < COMP_CWORD; i++)); do
        w=${COMP_WORDS[i]}
        if [[ $w == -* ]]; then
            # options given once are not offered again, unless they repeat
            [[ -z ${_j_rep["$key ${w%%=*}"]+set} ]] && used+="${w%%=*} "
            # skip the value of an option, given as "--opt value" or "--opt=value"
            if [[ -n ${_j_val["$key $w"]+set} ]]; then
                [[ ${COMP_WORDS[i+1]-} == = ]] && ((i++))
                ((i++))
            fi
        elif [[ " ${_j_sub[$key]-} ${_j_hid[$key]-} " == *" $w "* ]]; then
            key+=" $w"
            used=" "
        fi
    done
    # "--opt=" splits into "--opt" "=" "" since = is a word break
    if [[ $cur == = ]]; then
        cur=
    elif [[ $prev == = && $COMP_CWORD -ge 2 ]]; then
        prev=${COMP_WORDS[COMP_CWORD-2]}
    fi
    if [[ $prev == -* && -n ${_j_val["$key $prev"]+set} ]]; then
        _j_value "${_j_val["$key $prev"]}" "$cur"
        return 0
    fi
    _j_match "${_j_sub[$key]-}" "$cur"
    _j_match "${_j_opt[$key]-}" "$cur" "$used"
    if [[ $cur != -* && -n ${_j_pos[$key]+set} ]]; then
        _j_value "${_j_pos[$key]}" "$cur"
    fi
}

complete -o default -F _j_complete j
"""

_TABLES = ("_j_sub", "_j_hid", "_j_opt", "_j_pos", "_j_val", "_j_rep")


def _value_spec(param: click.Parameter) -> str:
    if isinstance(param.type, click.Choice):
        return "choice:" + "\n".join(str(c) for c in param.type.choices)
    if isinstance(param.type, click.Path) and param.type.dir_okay and not param.type.file_okay:
        return "dir"
    return "file"


def _add_params(cmd: click.Command, ctx: click.Context, key: str, tables: dict[str, dict[str, str]]) -> None:
    options = list(ctx.help_option_names) if cmd.add_help_option else []
    for param in cmd.params:
        if isinstance(param, click.Argument):
            tables["_j_pos"].setdefault(key, _value_spec(param))
            continue
        if not isinstance(param, click.Option):
            continue
        names = param.opts + param.secondary_opts
        if not param.hidden:
            options += names
        for name in names:
            if not param.is_flag and not param.count:
                tables["_j_val"][f"{key} {name}"] = _value_spec(param)
            if param.multiple or param.count:
                tables["_j_rep"][f"{key} {name}"] = "1"
    tables["_j_opt"][key] = " ".join(options)


def _add_command(cmd: click.Command, ctx: click.Context, key: str, tables: dict[str, dict[str, str]]) -> None:
    _add_params(cmd, ctx, key, tables)
    if not isinstance(cmd, click.Group):
        return
    visible, hidden = [], []
    for name in cmd.list_commands(ctx):
        try:
            sub = cmd.get_command(ctx, name)
            if sub is None:
                continue
            sub_ctx = click.Context(sub, parent=ctx, info_name=name, **sub.context_settings)
            _add_command(sub, sub_ctx, f"{key} {name}", tables)
        except Exception:
            logger.debug("Leaving %s %s out of bash completion", key, name, exc_info=True)
            continue
        (hidden if sub.hidden else visible).append(name)
    if visible:
        tables["_j_sub"][key] = " ".join(visible)
    if hidden:
        tables["_j_hid"][key] = " ".join(hidden)


def bash_completion_script(cli: click.Command) -> str:
    """Return a bash script that registers completion for ``j`` over ``cli``."""
    tables: dict[str, dict[str, str]] = {name: {} for name in _TABLES}
    _add_command(cli, click.Context(cli, info_name="j", **cli.context_settings), "j", tables)
    lines = [_BASH_FUNCTIONS.lstrip("\n")]
    for name, entries in tables.items():
        items = " ".join(f"[{shlex.quote(k)}]={shlex.quote(v)}" for k, v in entries.items())
        lines.append(f"declare -gA {name}=({items})\n")
    return "".join(lines)
