# workproof

Guardrails for Claude Code that enforce instead of advise. One plugin, several modules, each switchable:

- a **secret guard** that blocks shell and Docker commands that would read or print a secret, and a commit command that refuses secrets;
- **proof plans**: one plan per project whose stages close only when a named command passes;
- **agent roles** with a model limit each, optional **commit rules**, a **flow map** kept true by its tests, a watcher for **long runs**, and skills for closing a delivery, Docker Compose, Rust and button layout.

```
/plugin marketplace add devBertini/workproof
/plugin install workproof --marketplace devBertini/workproof
```

Everything a user needs is in [plugins/workproof](plugins/workproof): what each module does, what the plugin runs, reads and writes (it sends nothing), the options, troubleshooting and limits.

- Changes: [CHANGELOG.md](CHANGELOG.md)
- Reporting a vulnerability: [SECURITY.md](SECURITY.md)
- Questions and bugs: [issues](https://github.com/devBertini/workproof/issues)

## Development

`python -B scripts/check_all.py` is the one gate: every Python suite under `tests/` and `scripts/`, `claude plugin test`, `claude plugin validate --strict`, a leak check and static checks of the package. It needs Python 3.10 or later and, for the plugin checks, the `claude` command. `tools/` holds development tools that are not part of the plugin.

License: MIT.
