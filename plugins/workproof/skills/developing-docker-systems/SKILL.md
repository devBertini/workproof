---
name: developing-docker-systems
description: Builds, tests and runs a Docker Compose system - proves a change in a throwaway Compose project with a test profile, runs parallel lanes in worktrees under their own project names, brings the system's own stack up without clashing, builds lean images, and keeps secrets out of every output. Use when building, changing, testing, validating or running a system defined by Dockerfiles and Compose files, or inspecting its containers and compose configuration.
---

# Developing Docker systems

If `${user_config.docker}` is false, this module is off: ignore this skill.

Each system's own commands live in its `CLAUDE.md`.

## Proving a change

1. `docker compose config -q --no-interpolate` checks the files without printing the interpolated config, which holds secrets.
2. Build only the images the change reaches: `docker compose -p <system>-<id> --profile test build <runner> <services it starts>`. A build or run over a minute goes to the background (skill `workproof:running-long-commands`).
3. Run the tests in their container, bounded: `docker compose -p <system>-<id> --profile test run --rm <runner> [paths]`; read the JUnit file or the runner's summary, not only its exit code.
4. A network feature: bring the stack up and `curl -fsS` its endpoint; a one-shot service exits, so check it with `docker compose ps`, not `up --wait`.
5. `docker compose -p <system>-<id> --profile test down` once the stack is spent; add `-v` only when that stack made a volume (`docker volume ls --filter label=com.docker.compose.project=<system>-<id> -q` lists it), in a command of its own. Never `-v` on the system's own long-lived stack.

On Windows, absolute `-f` paths and override volumes start with an upper-case drive letter (`C:/`): a lower-case one can make BuildKit refuse with `additional privileges requested`, because the Dockerfile path no longer matches the context. In Git Bash, a container path argument (`-w /srv`) is rewritten to a Windows path: prefix the command with `MSYS_NO_PATHCONV=1`.

## Images

Docker's build best practices (docs.docker.com/build/building/best-practices/):

- Multi-stage: one stage fetches dependencies from the lockfile alone (cached until it changes), one builds, and each final stage copies only what runs plus production dependencies.
- Bases pinned by a supported version and digest (`node:<LTS>-slim@sha256:...`), never `latest` or an end-of-life release; update the digest with `--pull`.
- Installs from the lockfile only (`npm ci`, `pnpm install --frozen-lockfile`).
- Final stages run as a non-root `USER`; Compose adds `read_only`, `cap_drop: [ALL]` and `no-new-privileges`. Files copied into a volume with `docker compose cp` land owned by root: hand them to the service's user with a one-off `chown` run as root with `--cap-add CHOWN`.
- Health checks run with a tool the image already has; never install `curl` only for them.
- `.dockerignore` keeps `.env`, `.git`, dependency folders and build output out of the context.

## Lanes in parallel

- Commit first, then `git worktree add ../.<system>-worktrees/<id> -b lane/<id>`.
- Each lane runs everything under `-p <system>-<id>`, publishes no port and fixes no subnet.
- A lane comes back with `git merge --no-ff lane/<id>`, so `git branch -d` can remove it; then `git worktree remove`.

## Before `up` of the system's own stack

- Check each published port is free and see what else runs (`docker ps -a`).
- Check the Docker engine answers (`docker info`); stop it at the end only if this session started it and nothing else runs.

## Secrets

The Docker commands that print secrets, and what to run instead, are in skill `workproof:guarding-secrets`; with the secret guard on (`${user_config.secret_guard}`) its hook blocks them. A block means the command would print a secret: change the command, never work around the hook.
