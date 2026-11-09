# Security

## Reporting a vulnerability

Report it privately through GitHub: on the repository page open **Security**, then **Report a vulnerability**. Please do not open a public issue for a way around the secret guard that goes beyond the limits the README already lists, or for anything that makes the plugin run, read or send something it does not document.

Say what you ran, on which system and Claude Code version, and what happened. Reports are read at least once a month; a fix ships as a new version with a line in the changelog.

## What the plugin is and is not

The secret guard matches patterns in commands and file names. It protects against mistakes, not against a command built to hide what it touches, and it is not a sandbox: the plugin's README lists its limits. A command that those limits already describe as getting past the guard is a known limit, not a vulnerability; ideas that narrow one are welcome as ordinary issues.

The plugin makes no network call and collects no data. Everything it runs, reads and writes is listed in `plugins/workproof/README.md`.

## Supported versions

The latest release.
