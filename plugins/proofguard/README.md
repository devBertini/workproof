# proofguard

Guardrails for Claude Code that enforce instead of advise. One plugin, two modules, each on by default and switchable in `/config`:

| Module | What it does |
|---|---|
| Secret guard | Blocks Bash and PowerShell commands that read or print secrets, Docker commands that leak them, and commits that stage them. |
| Proof plans | Keeps one plan per project; a stage closes only when a named command passes, and the plan is printed back at every session start. |

## Install

```
/plugin install proofguard --marketplace devBertini/proofguard
```

Requires Python 3.10 or later on PATH (`python3`, `python` or `py -3`). On Windows the hooks run through Git Bash, which Claude Code uses by default.

## Options

| Option | Default | Effect |
|---|---|---|
| `secret_guard` | on | The secret guard hook. |
| `extra_secret_names` | empty | More file name patterns for the secret guard. |
| `proof_plans` | on | The session-start plan hook. |
| `plans_root` | empty | A folder of many projects whose active plans are listed at session start. |

It also adds two skills, `proofguard:secret-guard` and `proofguard:working-in-stages`, which Claude loads when a block or a multi-step task calls for them.

Claude Code asks for them when the plugin is enabled and shows them in `/config`. The command line tools (`work-plan`, `verify-run`, `guarded-commit`) stay available whichever modules are on.

## Secret guard

The secret guard stops the shell from reading or printing secrets. A hook inspects every Bash and PowerShell command before it runs and blocks the ones that would read a credential file, print a container's environment or leak a compose file's values. A small command, `guarded-commit`, commits only the paths you name and refuses when a secret would go in.

It is a pattern guard against mistakes, not a sandbox. See [Limits](#limits).

### What it blocks

A command is blocked, with exit code 2 and the reason on stderr for Claude, when it:

- is given the name of a secret file: `.env` and `.env.*` (but not `.env.example`, `.env.sample` or `.env.template`), private keys (`id_rsa`, `id_ed25519` and siblings, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.p8`), keystores, and credential files of AWS, Google Cloud, Azure, Docker, Kubernetes, npm, PyPI, netrc, pgpass, htpasswd, GitHub CLI, GnuPG and Git, plus service-account and OAuth client files and Terraform state;
- hands such a name to an interpreter: `python -c "open('.env')"`, a heredoc fed to `python` or `node`, `bash -c '...'`, even through nesting;
- runs one of the Docker leaks: `docker compose config` without `--no-interpolate`, `docker inspect` without a `--format` that leaves out `Config.Env`, or `env`, `printenv` and `/proc/*/environ` inside a container.

Examples:

| Command | Result |
| --- | --- |
| `cat .env` | blocked |
| `cp ~/.aws/credentials /tmp/x` | blocked |
| `python -c "print(open('id_rsa').read())"` | blocked |
| `docker compose config` | blocked, use `--no-interpolate` |
| `docker exec db printenv` | blocked |
| `ls -la .env`, `test -f .env`, `git check-ignore .env` | allowed (names only) |
| `docker compose --env-file .env up -d` | allowed |
| `cp .env.example .env` | allowed (writes, prints nothing) |
| `cat .env.example` | allowed |

### Options

Its extra option, set when the plugin is enabled or in `/config`:

- `extra_secret_names`: file name patterns to guard besides the built-in list, separated by commas or new lines (a JSON array also works). `*` stays inside one folder, `**` crosses folders, and a pattern matches the end of a path, so `*.vault`, `deploy/keys/**` and `.my-token` all work. Matching ignores case.

Claude Code hands the value to the hook as the environment variable `CLAUDE_PLUGIN_OPTION_EXTRA_SECRET_NAMES` ([plugins reference](https://code.claude.com/docs/en/plugins-reference)). It reaches hook processes only, so `guarded-commit`, which runs as an ordinary command, applies the extra patterns only when that variable is present in its environment.

### guarded-commit

```
guarded-commit <repo> "<message>" <path>...
```

Plugin `bin/` folders are on the Bash tool's `PATH`, so Claude can run it by name. It stages exactly the paths given, then refuses (exit 1, reason on stderr, nothing committed, nothing left staged) when:

- a staged file name matches a secret file name;
- the staged content, read from the index and not the working copy, matches a token format: GitHub, AWS, Slack, Stripe live, Anthropic, OpenAI and Hugging Face tokens, and private key blocks. The report gives file, line and kind, never the value. A Google API key only warns;
- something else is already staged, or nothing changes in those paths.

Otherwise it commits with the message as written. It checks no message style and never pushes. Binary files and files over 2 MB are skipped with a note.

### Measured

On one developer's local Claude Code transcripts, 40,967 Bash and PowerShell commands were replayed through the guard and every block was reviewed: 17 blocks, 9 intended and 8 false (0.02% of commands). The false blocks were interpreter code that only mentions a secret file name while editing a text file, a container `env | grep` on a public image, and a project-level `.npmrc` read. The guard called through Git Bash and the launcher on Windows 11 takes a median of 71 ms and a p95 of 77 ms (200 calls).

### Limits

- It matches patterns in the command text. A command built to hide a file name (concatenating a name, reading it from a variable, an encoded string) gets past it. It guards against mistakes, not against an adversary.
- It is not a sandbox. Where Claude Code's sandbox runs, turn it on as well: it can deny reads of listed files and environment variables at the operating-system level, which a pattern cannot. It does not run on native Windows, where this hook is the only layer.
- Interpreter code that merely mentions a secret file name, for example a script that edits a text file containing the name, is blocked too: the guard cannot tell a mention from a read.
- It sees commands, not file contents: a secret that reaches the conversation by another route (a file tool, a copied value) is outside it.
- If the hook cannot start (no Python), Claude Code treats that as a non-blocking error and the command runs.
- There is no support promise: issues are looked at monthly.

## Proof plans

Proof plans keeps one plan per project in a JSON file and makes a stage close only on proof. You split the work into stages, each with a "done when" that names its proof. To close a stage you hand `work-plan` the proof command; it runs the command and closes the stage only if the command exits 0, storing the evidence (command, exit code, duration, a hash of the whole output, its last lines). The plan is printed back at the start of every session, after `/clear` and after a compaction, so work resumes where it stopped.

### An example session

```
$ work-plan new --goal "Add CSV export to the report page"
$ work-plan add --title "Export module" --done-when "pytest tests/test_export.py passes"
$ work-plan add --title "Wire the button" --done-when "the e2e test for the export button passes"
$ work-plan start 1
$ work-plan note 1 "dates must be ISO 8601; the report model already has to_rows()"
$ work-plan done 1 --result "export module and tests added" --bundle -- pytest tests/test_export.py
ACTIVE PLAN /work/app/docs/ACTIVE-PLAN.json: 1 done, 0 dropped, 1 open (`work-plan show` lists every stage)
  [done] 1. Export module [verified]: export module and tests added (proof: verified: pytest tests/test_export.py exit 0 in 2.4 s)
  [todo] 2. Wire the button | done when: the e2e test for the export button passes
  next: stage 2, Wire the button
```

If the test had failed, stage 1 would have stayed open and the command's last lines would have been printed. In a later session the hook prints the plan, with the notes of the stage in progress, before you type anything.

### Commands

`work-plan` works on the plan of the current project, or of `--where <folder>`.

| Command | What it does |
| --- | --- |
| `new --goal "<text>"` | Creates the plan (refused if one exists). |
| `add --title "<t>" --done-when "<proof>"` | Appends a pending stage. |
| `start N` | Marks a stage in progress; only one at a time. |
| `note N "<text>"` | Appends a note to an open stage. |
| `done N --result "<r>" --bundle [--timeout S] -- <command...>` | Runs the command from the project folder (default 600 s) and closes the stage only on exit 0. |
| `done N --result "<r>" --proof "<text>"` | Closes a stage on a proof that is not a command. Not combinable with `--bundle`. |
| `pause N --reason "<r>"` | Puts a stage in progress back to pending, with a note. |
| `drop N --reason "<r>"` | Marks an open stage dropped. |
| `show` | Prints the whole plan (read only). |
| `finish` | Deletes the plan once every stage is closed or dropped. |
| `hook` | The SessionStart hook; reads Claude Code's hook JSON on stdin. |

`done` and `drop` erase the stage's notes; put what must last into `--result`. Exit status is 0 on success and 1 with a message on stderr when a command is refused or fails, including a `--bundle` command that does not exit 0.

`verify-run` is the engine behind `--bundle` and can be used alone:

```
verify-run run [--cwd <dir>] [--timeout N] [--shell] -- <command...>   # prints one JSON object
verify-run check <json | file | ->                                      # validates such an object
```

`run` exits 0 when the command passed, 1 when it failed or timed out, 2 when it was refused or misused. A pipeline fails when any of its stages fails. It never runs a command carrying `--execute`, a `git ... push`, or an `rm` with a recursive flag. This is a deliberately conservative check on the words of the command line, not a sandbox.

### Where the project is

The project is the git top-level folder of the current folder (or of `--where`); outside a git repository it is the folder itself. The plan is `<project>/docs/ACTIVE-PLAN.json`.

### Configuration

One optional setting, `plans_root`: a folder that holds many projects. Claude Code asks for it when you enable the plugin and exports it to the hook as `CLAUDE_PLUGIN_OPTION_PLANS_ROOT`. Commands run through the Bash tool do not receive plugin options, so for the CLI set the environment variable `PROOF_PLANS_ROOT` (it wins over the option).

With a plans root:

- a session started outside any single project (for example in the root folder) gets the main plan in full and one index line per other active plan under the root, up to three folders deep;
- plan labels are relative to the root (without one, the label is the absolute path);
- the root folder's own plan is `<root>/.claude/ACTIVE-PLAN.json`;
- each plan-changing command records which plan the session works on in `<root>/.claude/plan-sessions/<session id>` (pruned after 14 days), and the hook follows it.

Without one, the hook prints only the current project's plan, and nothing else is written outside the project. Set `PROOF_PLANS_SILENT=1` to make the hook print nothing.

### File format

```json
{
 "goal": "Add CSV export to the report page",
 "created": "2026-01-15",
 "stages": [
  {"id": 1, "title": "Export module", "done_when": "pytest passes", "status": "done",
   "notes": [], "result": "export module added", "proof": "verified: pytest exit 0 in 2.4 s",
   "verified": {"command": "pytest", "exit": 0, "duration_s": 2.4, "sha256": "...", "tail": "...", "time": "..."}},
  {"id": 2, "title": "Wire the button", "done_when": "e2e passes", "status": "pending", "notes": []}
 ]
}
```

Status is `pending`, `in_progress`, `done` or `dropped`. Stages are only ever marked, never removed. The file is written with LF line ends and UTF-8.

### Limits

- The hook output is capped at 9,500 characters (Claude Code keeps 10,000). Over that, older closed stages fold into one line, then the oldest notes, then index lines, then texts are clipped.
- `--bundle` proves that a command exited 0, not that the command tests the right thing; the done-when text is where that is decided.
- The refusal check can refuse a harmless command whose words look dangerous (`git log --grep push`).
- A plan label with spaces in a path outside the plans root cannot be parsed by tools that read the output of `work-plan`; they should read the file instead.
- Plugin `bin/` commands are not installed by claude.ai; use Claude Code.

## License

MIT
