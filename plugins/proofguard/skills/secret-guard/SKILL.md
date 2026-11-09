---
name: secret-guard
description: How to work with the secret-guard hook. Use when a Bash or PowerShell command was blocked as "Blocked by proofguard (secret-guard module)", before reading or copying credential files, .env files, keys or tokens, before inspecting Docker containers or compose files, and before committing files that may hold secrets.
---

# Working with the secret guard

The proofguard secret guard hook inspects every Bash and PowerShell command before it runs and blocks the ones that would read or print a secret. A block is the guard working, not an error to route around.

## What it blocks

- A command that is handed the name of a credential file: `.env` and `.env.*` (not `.env.example`, `.env.sample`, `.env.template`), private keys (`id_rsa` and its siblings, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.p8`), keystores, cloud and tool credential files (`~/.aws/credentials`, `~/.docker/config.json`, `~/.kube/config`, `.npmrc`, `.pypirc`, `.netrc`, `.git-credentials`, and similar), service-account and OAuth client files, and any pattern the user added in the plugin options.
- Interpreter code that names such a file: `python -c "open('.env')"`, a heredoc fed to `python` or `node`, `bash -c '...'`.
- Docker commands that print secrets: `docker compose config` without `--no-interpolate`, `docker inspect` without a `--format` that leaves out the environment, and `env`, `printenv` or `/proc/*/environ` run inside a container.

## What stays allowed

Listing or testing a file (`ls`, `test -f`, `stat`, `chmod`, `git check-ignore`, `git status`), handing an env file to Docker (`docker compose --env-file .env up`), `git rm --cached`, copying a template over a secret file (`cp .env.example .env`), and every command that does not name a secret file.

## How to proceed

1. Refer to a secret by its path, never by its value. Say "the token in `~/.config/tool/credentials`", do not open it.
2. Let the tool that needs the secret read it: pass the env file to Docker with `--env-file`, give the CLI its config path, run the program that loads the file itself.
3. Never print, copy, echo or paste a secret into a command, a file, a commit message or the conversation. If a task needs a value you cannot see, ask the user to supply it through the tool.
4. For Docker, use `docker compose config --no-interpolate`, `docker inspect --format '{{.State.Status}}' <name>` (a format that does not touch `Config`), and `docker logs`.
5. If a block looks wrong because the file holds no secret, ask the user. Do not rename the file, build the name from pieces or use another program to get around the guard.

## Committing

Use `guarded-commit <repo> "<message>" <path>...`. It stages exactly the paths given and commits them, and it refuses (non-zero exit, reason on stderr, nothing committed, nothing left staged) when a staged file name matches a secret file name or the staged content matches a known token format (GitHub, AWS, Slack, Stripe, Anthropic, OpenAI, Hugging Face tokens, private key blocks). The report names the file, line and kind, never the value. It refuses when something else is already staged. It does not push and it imposes no message style.

If it refuses, remove the secret from the file (read it from the environment instead), add the file to `.gitignore` if it should never be committed, and run it again.

## Limits

The guard matches patterns in commands. It protects against mistakes, not against a command built to hide what it touches, and it is not a sandbox.
