"""Docker rules of the guard: commands that print a container system's secrets.

Refused: `docker compose config` (or its older name `convert`) without `--no-interpolate` or with `--environment` (they print interpolated values or
the environment), `docker inspect` (also `service inspect`) without a `--format` that leaves out the environment
(`Config.Env`; `--format json` is the whole object), and `env`, `printenv`, `/proc/*/environ` or a file under
`/run/secrets/` read inside a container (`exec`, `run`). Python 3.10+, standard library only.
"""
import os
import re

# Global options of `docker` and `docker compose` that take a value; the word after them is not the subcommand.
VALUED = {"-f", "--file", "-p", "--project-name", "--profile", "--env-file", "--project-directory", "--ansi",
          "--progress", "--parallel", "-c", "--context", "-H", "--host", "--config", "-l", "--log-level"}
# Flags of `compose config` that print names or hashes only.
CONFIG_NAMES_ONLY = {"--no-interpolate", "--services", "--volumes", "--profiles", "--images", "--networks", "--hash",
                     "-q", "--quiet", "--help", "-h"}
# `convert` is the older name of `compose config`.
CONFIG_COMMANDS = {"config", "convert"}
WHOLE_OBJECT = re.compile(r"\{\{\s*(json\s+)?\.(Config)?\s*\}\}")
ENV_READERS = {"env", "printenv"}
RUN_SECRET = re.compile(r"/run/secrets/[^/\s]+")
# Programs that print a file given to them; an app that only uses a secret file inside the container passes.
FILE_PRINTERS = {"cat", "head", "tail", "less", "more", "strings", "xxd", "od", "hexdump", "base64", "grep", "awk",
                 "sed", "tac", "nl"}


def subcommand(args):
    """The first word that is not a global option (or an option's value), and the words after it."""
    i = 0
    while i < len(args):
        if args[i] in VALUED:
            i += 2
        elif args[i].startswith("-"):
            i += 1
        else:
            return args[i], args[i + 1:]
    return "", []


def format_value(args):
    for i, word in enumerate(args):
        if word in ("-f", "--format") and i + 1 < len(args):
            return args[i + 1]
        for prefix in ("--format=", "-f="):
            if word.startswith(prefix):
                return word[len(prefix):]
    return None


def pieces_of(args):
    """The words of a container command, a script given as one word (`sh -c '...'`) split into its own."""
    return [p for word in args for p in re.split(r"[\s;|&()]+", word) if p]


def reads_environment(args):
    pieces = pieces_of(args)
    return any(os.path.basename(p) in ENV_READERS or re.search(r"/proc/[^/\s]+/environ", p) for p in pieces)


def docker_verdict(program, args):
    """The reason a `docker` or `docker-compose` command is refused, or None. Any other program passes."""
    if program == "docker":
        sub, rest = subcommand(args)
        compose = sub == "compose"
        if compose:
            sub, rest = subcommand(rest)
    elif program == "docker-compose":
        compose = True
        sub, rest = subcommand(args)
    else:
        return None
    if sub in ("container", "image") and rest:
        sub, rest = rest[0], rest[1:]
    if compose and sub in CONFIG_COMMANDS and "--environment" in rest:
        return "`docker compose config --environment` prints the environment"
    if compose and sub in CONFIG_COMMANDS and not CONFIG_NAMES_ONLY.intersection(rest):
        return "`docker compose config` prints the interpolated secrets; add `--no-interpolate`"
    if not compose and sub == "service" and rest[:1] == ["inspect"]:
        sub, rest = "inspect", rest[1:]
    if not compose and sub == "inspect":
        shape = format_value(rest)
        if shape is None:
            return "`docker inspect` without `--format` prints the environment (`Config.Env`)"
        if "Env" in shape or WHOLE_OBJECT.search(shape) or shape.strip().lower() == "json":
            return "this `docker inspect --format` prints the environment or the whole object"
    if sub in ("exec", "run") and reads_environment(rest):
        return "reading the environment inside a container (`env`, `printenv`, `/proc/*/environ`) prints secrets"
    if sub in ("exec", "run") and any(RUN_SECRET.search(p) for p in pieces_of(rest)) \
            and any(os.path.basename(p) in FILE_PRINTERS for p in pieces_of(rest)):
        return "reading a file under `/run/secrets/` inside a container prints a secret"
    return None
