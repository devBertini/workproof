// The watcher (module long_runs, option long_runs, on by default): the deferred tool watch_run follows a long
// run's log with scripts/watch_log.py, keeps its progress in the status line and wakes the session at most once per
// run: the first fatal line, a hang (no new line for `idle` seconds), a missing log or a watcher failure.
// The run's own end is not a wake: the background Bash command that writes the log already notifies.
import type { EngineInterface, Register } from 'claude-code'

const NAME = 'watch_run'
const TOOL = 'mcp__workproof__watch_run'
const END = 'DONE rc='
const IDLE_S = 1200
const STATUS_WIDTH = 100

const DESCRIPTION = [
  'Follows the log of a long run started as a background Bash command (skill workproof:running-long-commands)',
  'and wakes this session at most once: at the first line matching `fatal`, when the log stops growing for `idle`',
  'seconds, or when the log never appears. Lines matching `progress` only update the status line. The run\'s end is',
  'the background command\'s own notification, so no Monitor is needed beside it. Answers at once.',
].join(' ')

const SCHEMA = {
  type: 'object',
  properties: {
    log: { type: 'string', description: 'Absolute path of the log the run writes, ending with its end line.' },
    fatal: { type: 'string', description: 'Regular expression of a line worth stopping for, e.g. "error:|Fatal error|FAILED".' },
    progress: { type: 'string', description: 'Regular expression of lines shown in the status line only.' },
    end: { type: 'string', description: `Regular expression of the run's last line (default "${END}").` },
    pid: { type: 'integer', description: 'PID of the run; a writer gone without its end line ends the watch quietly.' },
    idle: { type: 'number', description: `Seconds without a new line that count as a hang (default ${IDLE_S}).` },
  },
  required: ['log', 'fatal'],
}

type Input = { log: string; fatal: string; progress?: string; end?: string; pid?: number; idle?: number }
type Watch = { name: string; line: string; hasWoken: boolean }

// What the watch-log line means for this watch.
export type Kind = 'end' | 'fatal' | 'progress' | 'gone' | 'idle' | 'nolog' | 'other'

const watches = new Map<number, Watch>()
const pending: string[] = []
let nextId = 1
let python: Promise<string[] | undefined> | undefined

export function classify(line: string, end: RegExp, fatal: RegExp, progress?: RegExp): Kind {
  if (/^WRITER EXITED pid \d+/.test(line)) return 'gone'
  if (/^IDLE [\d.]+ s, no new line in /.test(line)) return 'idle'
  if (/^NO LOG .+ after [\d.]+ s$/.test(line)) return 'nolog'
  if (end.test(line)) return 'end'
  if (fatal.test(line)) return 'fatal'
  if (progress?.test(line)) return 'progress'
  return 'other'
}

// The watch-log argument vector after the interpreter; `--events` carries both patterns, the module tells them apart.
export function argvOf(interpreter: readonly string[], root: string, input: Input): string[] {
  const events = input.progress ? `(?:${input.fatal})|(?:${input.progress})` : input.fatal
  const argv = [...interpreter, '-B', `${root}/scripts/watch_log.py`, input.log,
    '--events', events, '--end', input.end ?? END, '--idle', String(input.idle ?? IDLE_S)]
  if (input.pid !== undefined) argv.push('--pid', String(input.pid))
  return argv
}

// Why the input cannot be watched, or undefined.
export function problemOf(input: Input): string | undefined {
  if (typeof input.log !== 'string' || !/^(?:[A-Za-z]:[\\/]|\/)/.test(input.log)) return 'log must be an absolute path'
  for (const [key, value] of [['fatal', input.fatal], ['progress', input.progress], ['end', input.end]] as const) {
    if (value === undefined) continue
    try {
      new RegExp(value)
    } catch {
      return `${key} is not a regular expression: ${value}`
    }
  }
  return undefined
}

function baseName(path: string): string {
  return path.slice(path.lastIndexOf('/') + 1)
}

// The first Python 3 that runs, as the bin/ wrappers probe it (python3, python, py -3); the Windows Store stub fails.
function interpreter($: EngineInterface): Promise<string[] | undefined> {
  python ??= (async () => {
    for (const candidate of [['python3'], ['python'], ['py', '-3']]) {
      const ok = await $.process.run([...candidate, '-c', ''])
        .then(r => r.exitCode === 0, () => false)
      if (ok) return candidate
    }
    return undefined
  })()
  return python
}

function showStatus($: EngineInterface): void {
  const lines = [...watches.values()].map(w => `${NAME} ${w.name}: ${w.line}`.slice(0, STATUS_WIDTH))
  $.ui.status(lines.length > 0 ? lines.join(' | ') : undefined)
}

async function wake($: EngineInterface, text: string): Promise<void> {
  const sent = await $.prompt.submit({ text }).catch(() => ({ drop: 'rejected' }))
  if (sent.drop !== undefined) pending.push(text)
}

async function follow($: EngineInterface, id: number, input: Input): Promise<void> {
  const watch = watches.get(id)
  if (watch === undefined) return
  const end = new RegExp(input.end ?? END)
  const fatal = new RegExp(input.fatal)
  const progress = input.progress ? new RegExp(input.progress) : undefined
  const exe = await interpreter($)
  if (exe === undefined) throw new Error('no working Python 3 found (tried python3, python, py -3)')
  const stream = $.process.spawn({ argv: argvOf(exe, $.plugin.root, input) })
  let partial = ''
  let stderr = ''
  let last = ''
  try {
    for await (const chunk of stream) {
      if (chunk.stream === 'stderr') {
        stderr = (stderr + chunk.text).slice(-2000)
        continue
      }
      const lines = (partial + chunk.text).split('\n')
      partial = lines.pop() ?? ''
      for (const line of lines) {
        last = line
        const kind = classify(line, end, fatal, progress)
        watch.line = line.trim()
        if (kind === 'fatal' && !watch.hasWoken) {
          watch.hasWoken = true
          void wake($, `${NAME}: first fatal line in ${input.log} (the run goes on; its end comes as the background command's notification): ${line}`)
        } else if (kind === 'idle' || kind === 'nolog') {
          void wake($, `${NAME}: ${line}`)
        }
        showStatus($)
      }
    }
    const { code } = await stream.result
    if (code !== 0 && code !== 3 && code !== 4 && code !== 5) {
      void wake($, `${NAME}: the watcher of ${input.log} failed (exit ${code}): ${stderr.trim().split('\n').pop() ?? last}`)
    }
  } finally {
    watches.delete(id)
    showStatus($)
  }
}

// At a turn end: a wake the prompt queue refused goes again.
export function afterTurn($: EngineInterface): void {
  const text = pending.shift()
  if (text !== undefined) void wake($, text)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.tool.register({ name: NAME, description: DESCRIPTION, inputSchema: SCHEMA, isDeferred: true })
    return started
  })

  on('tool.call', { tool: TOOL }, async ($, e) => {
    const input = e as unknown as Input
    const problem = problemOf(input)
    if (problem !== undefined) return { deny: `${NAME}: ${problem}` }
    const id = nextId++
    watches.set(id, { name: baseName(input.log), line: 'waiting for the log', hasWoken: false })
    showStatus($)
    void follow($, id, input).catch((error: unknown) => {
      watches.delete(id)
      showStatus($)
      return wake($, `${NAME}: the watcher of ${input.log} could not run: ${String(error)}`)
    })
    return { result: `Watching ${input.log}. This session is woken only at the first fatal line, a hang or a missing log; the end arrives as the background command's notification.` }
  }).catch(($, e, next) => ({ deny: `${NAME} failed (${next.error?.message ?? 'unknown'}); follow the log with the Monitor and watch-log instead.` }))
}
