import { describe, expect, test } from 'claude-code/testing'
import { argvOf, baseName, classify, problemOf } from './watch-run'
import { setStatus } from './status'

const TOOL = 'mcp__workproof__watch_run'
const END = /DONE rc=/
const FATAL = /error:|TEST FAILED/
const PROGRESS = /Test Case .* passed/

describe('classify', () => {
  test('tells each watch-log line apart', async () => {
    expect(classify('DONE rc=0', END, FATAL, PROGRESS)).toBe('end')
    expect(classify('Foo.swift:3: error: no such module', END, FATAL, PROGRESS)).toBe('fatal')
    expect(classify("Test Case '-[A b]' passed (0.1 seconds).", END, FATAL, PROGRESS)).toBe('progress')
    expect(classify('WRITER EXITED pid 42, no end line', END, FATAL, PROGRESS)).toBe('gone')
    expect(classify('IDLE 1200 s, no new line in /x/test.log', END, FATAL, PROGRESS)).toBe('idle')
    expect(classify('NO LOG /x/test.log after 120 s', END, FATAL, PROGRESS)).toBe('nolog')
    expect(classify('Compiling Foo.swift', END, FATAL)).toBe('other')
  })

  test('the end line wins over a fatal word in it', async () => {
    expect(classify('DONE rc=65 error: build failed', END, FATAL)).toBe('end')
  })
})

describe('argvOf and problemOf', () => {
  test('both patterns go to --events, pid and idle when given', async () => {
    const argv = argvOf(['python3'], '/plugin', { log: '/x/t.log', fatal: 'error:', progress: 'passed', pid: 7, idle: 60 })
    expect(argv.join(' ')).toBe('python3 -B /plugin/scripts/watch_log.py /x/t.log --events (?:error:)|(?:passed) --end DONE rc= --idle 60 --pid 7')
    expect(argvOf(['py', '-3'], '/p', { log: '/x/t.log', fatal: 'error:' }).join(' ')).toBe('py -3 -B /p/scripts/watch_log.py /x/t.log --events error: --end DONE rc= --idle 1200')
  })

  test('refuses a relative log and a broken pattern', async () => {
    expect(problemOf({ log: 't.log', fatal: 'x' })).toBe('log must be an absolute path')
    expect(problemOf({ log: '/t.log', fatal: '(' })).toBe('fatal is not a regular expression: (')
    expect(problemOf({ log: '/t.log', fatal: 'x', progress: 'ok' })).toBeUndefined()
  })
})

describe('the status line', () => {
  test('a log is named by its file, whichever separator the path uses', async () => {
    expect(baseName('/x/build/test.log')).toBe('test.log')
    expect(baseName('C:\\work\\build\\test.log')).toBe('test.log')
  })

  test('two mods share the one line: clearing a part keeps the other', async () => {
    const shown: (string | undefined)[] = []
    const engine = { ui: { status: (text: string | undefined) => void shown.push(text) } } as any
    setStatus(engine, 'a', 'watching')
    setStatus(engine, 'b', 'stage 2 closed')
    setStatus(engine, 'b', undefined)
    setStatus(engine, 'a', undefined)
    expect(shown).toEqual(['watching', 'watching | stage 2 closed', 'watching', undefined])
  })
})

// The engine beneath: the tool registry, the status line, the prompt queue, the Python probe and a watch-log run whose lines the test gives.
function engine(on: any, lines: string[], code: number, probe = 0) {
  const submitted: string[] = []
  const status: (string | undefined)[] = []
  const argvs: string[][] = []
  on('tool.register', (_: unknown, e: any) => ({ value: { tool: `mcp__workproof__${e.name}` } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('process.run', () => ({ value: { exitCode: probe, stdout: '', stderr: '' } }))
  on('ui.status', (_: unknown, e: any) => {
    status.push(e.text)
    return { value: undefined }
  })
  on('prompt.submit', (_: unknown, e: any) => {
    submitted.push(e.text)
    return { text: e.text }
  })
  on('process.spawn', async function* (_: unknown, e: any) {
    argvs.push([...e.argv])
    for (const line of lines) yield { stream: 'stdout', text: `${line}\n` }
    return { value: { code, signal: null } }
  })
  return { submitted, status, argvs }
}

// The watch runs detached from the tool call: let its promises run until the engine saw what the test waits for.
async function settle(_: any, done: () => boolean) {
  for (let i = 0; i < 2000 && !done(); i++) await Promise.resolve()
}

test('one wake for a run with several fatal lines, none at its end', async ($, on) => {
  const seen = engine(on, ["Test Case '-[A b]' passed (0.1 seconds).", 'A.swift:1: error: one', 'A.swift:2: error: two', 'DONE rc=65'], 0)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ran: any = await $.tool.call({ tool: TOOL, log: '/x/test.log', fatal: 'error:', progress: 'passed' } as any)
  expect(String(ran.result)).toContain('Watching /x/test.log')
  await settle($, () => seen.status.at(-1) === undefined && seen.status.length > 1)
  expect(seen.submitted.length).toBe(1)
  expect(seen.submitted[0]).toContain('A.swift:1: error: one')
  expect(seen.status.some(s => s?.includes('passed'))).toBe(true)
  expect(seen.status.at(-1)).toBeUndefined()
  expect(seen.argvs[0]?.[0]).toBe('python3')
})

test('a hang wakes once', async ($, on) => {
  const seen = engine(on, ['IDLE 60 s, no new line in /x/a.log'], 5)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await $.tool.call({ tool: TOOL, log: '/x/a.log', fatal: 'error:', idle: 60 } as any)
  await settle($, () => seen.submitted.length > 0)
  expect(seen.submitted).toEqual(['watch_run: IDLE 60 s, no new line in /x/a.log'])
})

test('a writer gone is quiet: the background command notifies', async ($, on) => {
  const seen = engine(on, ['WRITER EXITED pid 9, no end line'], 4)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await $.tool.call({ tool: TOOL, log: '/x/b.log', fatal: 'error:', pid: 9 } as any)
  await settle($, () => seen.status.at(-1) === undefined && seen.status.length > 1)
  expect(seen.submitted.length).toBe(0)
  expect(seen.argvs[0]?.includes('--pid')).toBe(true)
})

test('a relative log is refused with the reason', async ($, on) => {
  engine(on, [], 0)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ran: any = await $.tool.call({ tool: TOOL, log: 'test.log', fatal: 'error:' } as any)
  expect(ran.deny).toBe('watch_run: log must be an absolute path')
})
