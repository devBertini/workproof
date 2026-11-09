"""Splitting a shell command line into words and simple commands, shared by the guard's rule sets.

This is a lexer for guarding, not a shell: it follows quotes, operators, line continuations and backticks well
enough to find which program a command runs and which words it is given. Python 3.10+, standard library only.
"""
import os
import re
import shlex

OPERATORS = {";", ";;", "&", "&&", "|", "||", "|&", "(", ")", "\n"}
WRAPPERS = {"sudo", "command", "exec", "time", "nice", "nohup", "builtin"}


def tokens(text):
    """The words and operators of a command line. A backtick or a newline ends a simple command."""
    text = text.replace("\\\n", " ").replace("`", " ; ").replace("\n", " ; ")
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:  # an unbalanced quote: fall back to a coarse split
        return re.findall(r"[^\s;|&()]+|[;|&()\n]", text)


def segments(words):
    """The words grouped into simple commands, split at the operators."""
    out = [[]]
    for word in words:
        if word in OPERATORS:
            out.append([])
        else:
            out[-1].append(word)
    return [s for s in out if s]


def program_of(segment):
    """(program name, its arguments) after variable assignments and wrappers such as sudo or env."""
    i = 0
    while i < len(segment):
        word = segment[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word) or word in WRAPPERS:
            i += 1
        elif word == "env":
            i += 1
            while i < len(segment) and (segment[i].startswith("-") or "=" in segment[i]):
                i += 1
        else:
            name = os.path.basename(word.replace("\\", "/"))
            return (name[:-4] if name.lower().endswith(".exe") else name), segment[i + 1:]
    return "", []
