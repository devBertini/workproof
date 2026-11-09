// Claude Code keeps one status line per plugin, and two mods of this plugin write one: each sets its own part here
// and the line shows them side by side, so one mod clearing its part never wipes the other's.
import type { EngineInterface } from 'claude-code'

const parts = new Map<string, string>()

export function setStatus($: EngineInterface, key: string, text: string | undefined): void {
  if (text === undefined) parts.delete(key)
  else parts.set(key, text)
  $.ui.status(parts.size > 0 ? [...parts.values()].join(' | ') : undefined)
}
