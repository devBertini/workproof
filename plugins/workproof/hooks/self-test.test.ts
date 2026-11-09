import { describe, expect, mock, test } from 'claude-code/testing'
import { diagnose, lineOf, shellsOf } from './self-test'

const POSIX = '/home/me/.claude/plugins/workproof'
const WIN = 'C:\\Users\\me\\.claude\\plugins\\workproof'

describe('shellsOf and lineOf', () => {
  test('sh elsewhere; on Windows the Git Bash Claude Code was given, then Git\'s usual folder', async () => {
    expect(shellsOf(POSIX, undefined)).toEqual(['sh'])
    expect(shellsOf(WIN, 'D:\\Git\\bin\\bash.exe')).toEqual(['D:\\Git\\bin\\bash.exe', 'C:\\Program Files\\Git\\bin\\bash.exe'])
    expect(shellsOf(WIN, ' ')).toEqual(['C:\\Program Files\\Git\\bin\\bash.exe'])
  })

  test('the line is the hook command, with forward slashes and the path quoted', async () => {
    expect(lineOf(WIN)).toBe('"C:/Users/me/.claude/plugins/workproof/hooks/run-hook.cmd" modules.py --self-test')
  })
})

describe('diagnose', () => {
  test('a launcher that starts says nothing', async () => {
    expect(diagnose(POSIX, { exitCode: 0, stdout: 'workproof self-test ok: Python 3.12\n', stderr: '' })).toBeUndefined()
  })

  test('no Python names the install and the way to keep working', async () => {
    const text = diagnose(POSIX, { exitCode: 1, stdout: '', stderr: 'run-hook: no Python 3 found on PATH, tried python3 and python\n' })
    expect(text).toContain('no Python 3 on PATH')
    expect(text).toContain('Install Python 3.10 or later')
    expect(text).toContain('blocks every shell command and file read')
    expect(text).toContain('secret_guard off in /config')
    expect(text).toContain('workproof-doctor')
  })

  test('an old Python, a lost executable bit and an unknown failure each get their fix', async () => {
    expect(diagnose(POSIX, { exitCode: 1, stdout: '', stderr: 'workproof self-test: Python 3.8 is too old, the hooks need 3.10 or later\n' }))
      .toContain('(Python 3.8 is too old, the hooks need 3.10 or later)')
    expect(diagnose(POSIX, { exitCode: 126, stdout: '', stderr: `sh: 1: ${POSIX}/hooks/run-hook.cmd: Permission denied\n` }))
      .toContain(`chmod +x "${POSIX}/hooks/run-hook.cmd"`)
    expect(diagnose(POSIX, { exitCode: 3, stdout: '', stderr: '' })).toContain('the launcher exited 3')
  })

  test('a shell that cannot start means Git for Windows on Windows', async () => {
    expect(diagnose(WIN, { failed: 'ENOENT' })).toContain('Install Git for Windows')
    expect(diagnose(POSIX, { failed: 'ENOENT' })).toContain('Put `sh` on PATH')
    expect(diagnose(WIN, { failed: 'HooksError: $.process.run: timed out after 20000 ms' })).toBeUndefined()
  })
})

// The engine beneath: the launcher run answering as the test says, and the transcript lines.
function engine(on: any, run: (argv: string[]) => { exitCode: number; stdout: string; stderr: string } | undefined) {
  const logs: string[] = []
  const argvs: string[][] = []
  mock.env(on, {})
  on('tool.register', (_: unknown, e: any) => ({ value: { tool: `mcp__workproof__${e.name}` } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('process.run', (_: unknown, e: any) => {
    argvs.push([...e.argv])
    const answer = run(e.argv)
    return answer === undefined ? { deny: 'ENOENT' } : { value: answer }
  })
  on('ui.log', (_: unknown, e: any) => {
    logs.push(e.text)
    return { value: undefined }
  })
  on('ui.status', () => ({ value: undefined }))
  return { logs, argvs }
}

async function settle(done: () => boolean) {
  for (let i = 0; i < 2000 && !done(); i++) await Promise.resolve()
}

const START = { cwd: '/', surface: 'terminal', isInteractive: true } as any
const isSelfTest = (argv: string[]) => argv.at(-1)?.endsWith('modules.py --self-test') === true

test('a session whose launcher starts gets no line', async ($, on) => {
  const seen = engine(on, () => ({ exitCode: 0, stdout: 'workproof self-test ok: Python 3.12\n', stderr: '' }))
  await $.session.start(START)
  await settle(() => seen.argvs.some(isSelfTest))
  await settle(() => false)
  expect(seen.argvs.some(isSelfTest)).toBe(true)
  expect(seen.logs).toEqual([])
})

test('a session whose launcher finds no Python is told the cause and the fix, and still starts', async ($, on) => {
  const seen = engine(on, argv => (isSelfTest(argv)
    ? { exitCode: 1, stdout: '', stderr: 'run-hook: no Python 3 found on PATH, tried python3 and python\n' }
    : { exitCode: 0, stdout: '', stderr: '' }))
  const started: any = await $.session.start(START)
  expect(started.cwd).toBe('/')
  await settle(() => seen.logs.length > 0)
  expect(seen.logs.length).toBe(1)
  expect(seen.logs[0]).toContain('workproof: its hooks cannot start (no Python 3 on PATH)')
})

test('with the secret guard and every hook module off the self-test still runs once', { options: { long_runs: false, proof_plans: false } }, async ($, on) => {
  const seen = engine(on, () => ({ exitCode: 0, stdout: '', stderr: '' }))
  await $.session.start(START)
  await settle(() => seen.argvs.some(isSelfTest))
  expect(seen.argvs.filter(isSelfTest).length).toBe(1)
})
