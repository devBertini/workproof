# secret-guard

Work in progress. A Claude Code plugin that blocks Bash and PowerShell commands which read secret files or print credentials, Docker commands that would leak them, and commits that stage them. It is a pattern guard, not a sandbox: where Claude Code's own sandbox runs, turn it on as well.

## Install

```
/plugin install secret-guard --marketplace devBertini/prova
```
