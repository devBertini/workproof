"""Secret file names and secret content formats, shared by the shell hook and the commit tool.

SECRET_NAMES lists file names (matched against a path with `/` separators) that normally hold credentials.
SECRET_CONTENT lists token formats that their vendors publish; the matched value is never printed, only its kind.
Users add file names through the `extra_secret_names` plugin option or the WORKPROOF_EXTRA_SECRET_NAMES environment
variable (glob patterns; both apply), read by `extra_names_from_env`.

Python 3.10+, standard library only.
"""
import json
import os
import re

EXTRA_NAMES_ENV = "CLAUDE_PLUGIN_OPTION_EXTRA_SECRET_NAMES"
# The same list from a plain environment variable: plugin options live only in user settings and reach hooks alone,
# while a settings `env` entry reaches hooks, the Bash tool and guarded-commit, and can be set per project.
EXTRA_NAMES_PLAIN_ENV = "WORKPROOF_EXTRA_SECRET_NAMES"

# (regex, what the file is). Placeholder env files (.env.example and its siblings) hold no secret by convention:
# the names are allowed, the content is still scanned when such a file is committed.
SECRET_NAMES = [
    (r"(^|/)\.env(?!\.(example|sample|template)$)(\.[^/]*)?$", "environment file (.env)"),
    (r"\.pem$", "PEM key or certificate"),
    (r"\.key$", "private key file"),
    (r"(^|/)id_(rsa|dsa|ecdsa|ed25519)(_sk)?$", "SSH private key"),
    (r"\.(p12|pfx)$", "certificate bundle with its private key"),
    (r"\.p8$", "signing or push-notification private key (.p8, as issued by Apple developer services)"),
    (r"\.mobileprovision$", "provisioning profile"),
    (r"\.(keystore|jks)$", "Java or Android keystore"),
    (r"(^|/)[^/]*service_account[^/]*\.json$", "cloud service account key"),
    (r"(^|/)client_secret[^/]*\.json$", "OAuth client secret"),
    (r"(^|/)token\.json$", "OAuth token file"),
    (r"(^|/)[._]?netrc$", "credentials file (netrc)"),
    # `process.env` and `import.meta.env` are JavaScript, not files.
    (r"(^|/)(?!(process|meta)\.env$)[^/\s]+\.env$", "environment file (*.env)"),
    (r"(^|/)\.envrc$", "direnv environment file (.envrc)"),
    (r"(^|/)\.dev\.vars$", "local secrets file (.dev.vars)"),
    (r"(^|/)secrets\.toml$", "secrets file (secrets.toml)"),
    (r"(^|/)\.?credentials\.json$", "credentials file (credentials.json, including Claude Code's own)"),
    (r"(^|/)\.pgpass$", "PostgreSQL password file"),
    (r"(^|/)\.htpasswd$", "web server password file"),
    (r"(^|/)\.git-credentials$", "stored git credentials"),
    (r"(^|/)\.npmrc$", "npm configuration (may hold an auth token)"),
    (r"(^|/)\.pypirc$", "PyPI credentials file"),
    (r"(^|/)\.aws/credentials$", "AWS credentials"),
    (r"(^|/)\.config/gcloud/[^/]*(credentials|tokens)[^/]*$", "Google Cloud credentials"),
    (r"(^|/)\.azure/(accessTokens\.json|msal_token_cache[^/]*)$", "Azure credentials"),
    (r"(^|/)\.docker/config\.json$", "Docker registry credentials"),
    (r"(^|/)\.kube/config$", "Kubernetes client configuration"),
    (r"(^|/)\.config/gh/hosts\.ya?ml$", "GitHub CLI credentials"),
    (r"(^|/)\.gnupg/(private-keys-v1\.d|secring\.gpg)", "GnuPG private key"),
    (r"(^|/)terraform\.tfstate(\.backup)?$", "Terraform state (holds secret values)"),
]

# (level, kind, regex). FAIL refuses the commit; WARN only notes.
SECRET_CONTENT = [
    ("FAIL", "GitHub token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{50,255})"),
    ("FAIL", "private key block", r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"),
    ("FAIL", "AWS access key ID", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("FAIL", "Slack token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    ("FAIL", "Stripe live secret key", r"\b[rs]k_live_[0-9A-Za-z]{16,}"),
    ("FAIL", "Anthropic API key", r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    ("FAIL", "OpenAI API key", r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{40,}"),
    ("FAIL", "Hugging Face token", r"\bhf_[A-Za-z0-9]{30,}\b"),
    ("WARN", "Google API key (ships in apps by design; confirm it is restricted)", r"\bAIza[0-9A-Za-z_-]{35}\b"),
]


def glob_to_regex(glob):
    """Regex for a path glob: `*` and `?` stay inside one folder, `**` crosses folders; it matches a path suffix."""
    glob = glob.strip().replace("\\", "/")
    while glob.startswith("./"):
        glob = glob[2:]
    out = []
    i = 0
    while i < len(glob):
        char = glob[i]
        if glob.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        out.append("[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char))
        i += 1
    return "(^|/)" + "".join(out) + "$"


def split_globs(value):
    """The globs in an option value: one per line or comma separated, or a JSON array of strings."""
    value = (value or "").strip()
    if value.startswith("["):
        try:
            items = json.loads(value)
            if isinstance(items, list):
                return [str(item).strip() for item in items if str(item).strip()]
        except ValueError:
            pass
    return [part.strip() for part in re.split(r"[,\n]", value) if part.strip()]


def extra_names_from_env(environ=None):
    """Extra (regex, what) entries from the extra_secret_names option and WORKPROOF_EXTRA_SECRET_NAMES; a glob that
    cannot compile is skipped."""
    environ = os.environ if environ is None else environ
    extras = []
    globs = split_globs(environ.get(EXTRA_NAMES_ENV, "")) + split_globs(environ.get(EXTRA_NAMES_PLAIN_ENV, ""))
    for glob in dict.fromkeys(globs):
        try:
            re.compile(glob_to_regex(glob))
        except re.error:
            continue
        extras.append((glob_to_regex(glob), f"file matching your extra pattern `{glob}`"))
    return extras


def compile_names(extra=None):
    """Compiled (regex, what) for the built-in names plus extras. Case-insensitive: Windows and macOS file systems are."""
    return [(re.compile(pattern, re.IGNORECASE), what) for pattern, what in SECRET_NAMES + list(extra or [])]


def name_kind(path, names):
    """What secret file the path names, or None. `names` comes from compile_names."""
    path = path.replace("\\", "/")
    for pattern, what in names:
        if pattern.search(path):
            return what
    return None
