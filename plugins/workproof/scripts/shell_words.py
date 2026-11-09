"""Splitting a shell command line into words and simple commands, shared by the guard's rule sets.

This is a lexer for guarding, not a shell: it follows quotes, operators, comments, line continuations and backticks
well enough to find which program a command runs and which words it is given. Python 3.9+, standard library only.
"""
import os
import re
import shlex

OPERATORS = {";", ";;", "&", "&&", "|", "||", "|&", "(", ")", "\n"}
WRAPPERS = {"sudo", "command", "exec", "time", "nice", "nohup", "builtin"}
WORD_START = set(" \t;|&()<>")
# What an escaped parenthesis becomes before lexing: a word that is no operator (the fullwidth forms).
ESCAPED_PAREN = {"(": "（", ")": "）"}


def _ansi_c(text, i):
    """(the plain string, the index after it) for the ANSI-C quote `$'...'` that starts at text[i]."""
    out = []
    j = i + 2
    while j < len(text) and text[j] != "'":
        if text[j] == "\\" and j + 1 < len(text):
            j += 1
        out.append(text[j])
        j += 1
    return "".join(out), j + 1


def prepare(text):
    """The command with comments removed and ANSI-C quotes rewritten as single quotes, line by line.

    A `#` starts a comment only where bash would: unquoted, at the start of a word. Elsewhere (`issue#12`, `$#`,
    `https://x/#top`) it is an ordinary character."""
    out = []
    i = 0
    quote = None
    while i < len(text):
        char = text[i]
        if quote:
            out.append(char)
            if char == "\\" and quote == '"' and i + 1 < len(text):
                out.append(text[i + 1])
                i += 1
            elif char == quote:
                quote = None
        elif char == "\\" and i + 1 < len(text):
            # `\(` and `\)` are words given to the program (find's grouping), never the shell's subshell operators.
            out.append(ESCAPED_PAREN.get(text[i + 1], text[i:i + 2]))
            i += 1
        elif char == "$" and text.startswith("$'", i):
            plain, i = _ansi_c(text, i)
            out.append("'" + plain.replace("'", "'\"'\"'") + "'")
            continue
        elif char in "'\"":
            quote = char
            out.append(char)
        elif char == "#" and (i == 0 or text[i - 1] in WORD_START or text[i - 1] == "\n"):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        else:
            out.append(char)
        i += 1
    return "".join(out)


def tokens(text):
    """The words and operators of a command line. A backtick or a newline ends a simple command."""
    text = prepare(text.replace("\\\n", " ")).replace("`", " ; ").replace("\n", " ; ")
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:  # an unbalanced quote: fall back to a coarse split, without the quote marks
        return [w.replace("'", "").replace('"', "") for w in re.findall(r"[^\s;|&()]+|[;|&()\n]", text)]


def segments(words):
    """The words grouped into simple commands, split at the operators."""
    return [s for s, _ in pipeline(words)]


def pipeline(words):
    """(simple command, the operator after it or "") pairs, so a reader can see what a command pipes into."""
    out = [([], "")]
    for word in words:
        if word in OPERATORS:
            out[-1] = (out[-1][0], word)
            out.append(([], ""))
        else:
            out[-1][0].append(word)
    return [(s, op) for s, op in out if s]


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
                i += 2 if segment[i] in ("-u", "--unset", "-C", "--chdir") else 1  # these take a value
        else:
            name = os.path.basename(word.replace("\\", "/"))
            return (name[:-4] if name.lower().endswith(".exe") else name), segment[i + 1:]
    return "", []
