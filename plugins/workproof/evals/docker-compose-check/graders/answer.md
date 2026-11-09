---
type: regex
pattern: 'docker compose[^\n]*config[^\n]*(--no-interpolate|--quiet| -q\b)'
---

The command checks the files without printing interpolated secrets (--no-interpolate, or --quiet / -q, which print nothing).
