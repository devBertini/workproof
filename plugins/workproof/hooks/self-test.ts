// The session-start self-test: starts the hook launcher once, the way a command hook starts it (through a POSIX
// shell), and when it cannot start says why and what fixes it. The secret guard fails closed, so a launcher that
// cannot start blocks every shell command and file read: without this line the person sees only "hook error".
// It never blocks or delays the session: it runs after the session started and its own failure is silent.
import type { EngineInterface } from 'claude-code'

const GIT_BASH = 'C:\\Program Files\\Git\\bin\\bash.exe'
const TIMEOUT_MS = 20_000

export type Outcome = { exitCode: number; stdout: string; stderr: string } | { failed: string }

function isWindows(root: string): boolean {
  return /^[A-Za-z]:[\\/]/.test(root)
}

// The shells to try, in order: where hooks run through Git Bash, the path Claude Code was given and Git's usual one.
export function shellsOf(root: string, gitBash: string | undefined): string[] {
  if (!isWindows(root)) return ['sh']
  return [gitBash, GIT_BASH].filter((path): path is string => typeof path === 'string' && path.trim() !== '')
}

// The command line a hook runs, for `sh -c`.
export function lineOf(root: string): string {
  return `"${root.replaceAll('\\', '/')}/hooks/run-hook.cmd" modules.py --self-test`
}

// What to tell the person, or undefined when the launcher started and Python is new enough.
export function diagnose(root: string, outcome: Outcome): string | undefined {
  if ('exitCode' in outcome && outcome.exitCode === 0) return undefined
  const off = 'To work meanwhile, turn the option secret_guard off in /config. `workproof-doctor` runs every check.'
  const blocked = 'Until it is fixed the secret guard blocks every shell command and file read.'
  let cause: string
  let fix: string
  if ('failed' in outcome && /timed? ?out|still running/i.test(outcome.failed)) return undefined // slow, not broken
  if ('failed' in outcome) {
    cause = isWindows(root) ? 'Git Bash was not found' : `the shell could not start (${outcome.failed})`
    fix = isWindows(root) ? 'Install Git for Windows and restart Claude Code.' : 'Put `sh` on PATH and restart Claude Code.'
  } else {
    const detail = (outcome.stderr.trim() || outcome.stdout.trim()).split('\n').pop() ?? ''
    if (/no Python 3 found/.test(detail)) {
      cause = 'no Python 3 on PATH'
      fix = 'Install Python 3.10 or later (as python3, python or py -3; on Windows the Microsoft Store alias does not count).'
    } else if (/too old/.test(detail)) {
      cause = detail.replace(/^workproof self-test: /, '')
      fix = 'Install Python 3.10 or later and put it first on PATH.'
    } else if (/ermission denied/.test(detail)) {
      cause = 'the launcher lost its executable bit'
      fix = `Run: chmod +x "${root}/hooks/run-hook.cmd" "${root}"/bin/*`
    } else {
      cause = `the launcher exited ${outcome.exitCode}${detail === '' ? '' : `: ${detail}`}`
      fix = 'Reinstall the plugin.'
    }
  }
  return `workproof: its hooks cannot start (${cause}). ${blocked} ${fix} ${off}`
}

async function selfTest($: EngineInterface): Promise<void> {
  const root = $.plugin.root
  const gitBash = await $.env.get('CLAUDE_CODE_GIT_BASH_PATH').catch(() => undefined)
  let outcome: Outcome = { failed: 'no shell found' }
  for (const shell of shellsOf(root, gitBash)) {
    outcome = await $.process.run([shell, '-c', lineOf(root)], { timeoutMs: TIMEOUT_MS })
      .then(r => ({ exitCode: r.exitCode, stdout: r.stdout, stderr: r.stderr }), (error: unknown) => ({ failed: String(error) }))
    if ('exitCode' in outcome) break
  }
  const message = diagnose(root, outcome)
  if (message !== undefined) $.ui.log(message)
}

// Started at session start and never awaited: the session does not wait for it and its own failure is silent.
export function runSelfTest($: EngineInterface): void {
  void selfTest($).catch(() => undefined)
}
