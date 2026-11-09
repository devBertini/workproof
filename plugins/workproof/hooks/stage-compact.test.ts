import { describe, expect, mock, test } from 'claude-code/testing'
import { closedBy, instructionsOf, rootOf, verdictOf } from './stage-compact'

const ROOT = '/work/plans'
const PLAN = '/work/proj/docs/ACTIVE-PLAN.json'
const REL_PLAN = `${ROOT}/proj/docs/ACTIVE-PLAN.json`
const DONE_OUT = [
  `ACTIVE PLAN ${PLAN}: 2 done, 0 dropped, 1 open (\`work-plan show\` lists every stage)`,
  '  [done] 2. Build: the module [verified]: r (proof: verified: tsc exit 0 in 1.0 s)',
  '  [todo] 3. Proof: live | done when: x',
  '  next: stage 3, Proof: live',
].join('\n')
const REL_OUT = DONE_OUT.replace(`ACTIVE PLAN ${PLAN}`, 'ACTIVE PLAN proj/docs/ACTIVE-PLAN.json')

function plan(stages: object[]): string {
  return JSON.stringify({ goal: 'g', created: '2026-10-29', stages })
}
const done2 = { id: 2, title: 'Build', status: 'done', result: 'r', proof: 'p' }
const next3 = { id: 3, title: 'Proof: live', status: 'pending', notes: [] }

describe('closedBy', () => {
  test('an absolute label needs no plans root', async () => {
    expect(closedBy('work-plan done 2 --result "r" --bundle -- tsc -p x', DONE_OUT, '')).toEqual({ plan: PLAN, stage: 2 })
    expect(closedBy('cd p && work-plan done 2 --result r --proof p && git commit', DONE_OUT, ROOT)).toEqual({ plan: PLAN, stage: 2 })
    expect(closedBy('work-plan --where "/a b" done 2 --result r --proof p', DONE_OUT, '')).toEqual({ plan: PLAN, stage: 2 })
  })

  test('an absolute label with spaces or a Windows drive is kept as given', async () => {
    const spaced = DONE_OUT.replace(PLAN, 'C:\\my projects\\p\\docs\\ACTIVE-PLAN.json')
    expect(closedBy('work-plan done 2 --result r --proof p', spaced, '')?.plan).toBe('C:/my projects/p/docs/ACTIVE-PLAN.json')
  })

  test('a relative label is read from the plans root, and is nothing without one', async () => {
    expect(closedBy('work-plan done 2 --result r --proof p', REL_OUT, `${ROOT}/`)).toEqual({ plan: REL_PLAN, stage: 2 })
    expect(closedBy('work-plan done 2 --result r --proof p', REL_OUT, '')).toBeUndefined()
  })

  test('a piped done with no label takes the plan from the session pointer', async () => {
    const tail = '  [todo] 3. Proof: live\n  next: stage 3, Proof: live'
    const cmd = 'work-plan done 2 --result r --proof p 2>&1 | tail -2'
    expect(closedBy(cmd, tail, ROOT, 'C:\\x\\p\\docs\\ACTIVE-PLAN.json\n')).toEqual({ plan: 'C:/x/p/docs/ACTIVE-PLAN.json', stage: 2 })
    expect(closedBy(cmd, tail, ROOT, `${PLAN}\n`)).toEqual({ plan: PLAN, stage: 2 })
    expect(closedBy(cmd, tail, ROOT)).toBeUndefined()
    expect(closedBy(cmd, tail, ROOT, '  ')).toBeUndefined()
  })

  test('nothing for other commands or a done the output does not show', async () => {
    expect(closedBy('work-plan show', DONE_OUT, ROOT)).toBeUndefined()
    expect(closedBy('work-plan note 2 "done 2"', DONE_OUT, ROOT)).toBeUndefined()
    expect(closedBy('work-plan done 3 --result r --proof p', DONE_OUT, ROOT)).toBeUndefined()
    expect(closedBy('work-plan done 2 --result r --proof p', 'work_plan: stage 2 is pending; start it first', ROOT)).toBeUndefined()
  })
})

describe('rootOf', () => {
  test('the option wins, then PROOF_PLANS_ROOT, then none', async () => {
    expect(rootOf('D:\\P', '/env')).toBe('D:/P')
    expect(rootOf('', '/env')).toBe('/env')
    expect(rootOf(undefined, undefined)).toBe('')
  })
})

describe('verdictOf', () => {
  const ready = plan([done2, next3])

  test('compacts when the stage is done, nothing runs, a next stage waits and git is clean', async () => {
    expect(verdictOf(ready, 2, '')).toEqual({ act: 'compact', next: next3 })
  })

  test('forgets a stage that is not done with result and proof', async () => {
    expect(verdictOf(plan([{ ...done2, status: 'in_progress' }, next3]), 2, '').act).toBe('forget')
    expect(verdictOf(plan([{ ...done2, proof: '' }, next3]), 2, '').act).toBe('forget')
  })

  test('forgets once the next stage has started, or when nothing is left', async () => {
    expect(verdictOf(plan([done2, { ...next3, status: 'in_progress' }]), 2, ''))
      .toEqual({ act: 'forget', why: 'stage 3 is already in progress' })
    expect(verdictOf(plan([done2]), 2, '')).toEqual({ act: 'forget', why: 'no stage is left to do' })
  })

  test('defers on uncommitted changes, forgets a failed git or a missing plan', async () => {
    expect(verdictOf(ready, 2, ' M docs/ACTIVE-PLAN.json\n'))
      .toEqual({ act: 'defer', why: 'the repository has uncommitted changes' })
    expect(verdictOf(ready, 2, undefined).act).toBe('forget')
    expect(verdictOf(undefined, 2, '').act).toBe('forget')
  })

  test('the instructions name the closed stage, the next one and the plan', async () => {
    const text = instructionsOf({ plan: PLAN, stage: 2 }, next3 as any)
    expect(text).toContain('Stage 2 of the active plan')
    expect(text).toContain('stage 3 (Proof: live)')
    expect(text).toContain(PLAN)
  })
})

// The engine beneath: a Bash call answering the given output, the plan file, git, the context size and compaction.
type Files = { plan: string; git: string; tokens: number; stdout?: string; headless?: boolean; env?: Record<string, string>; pointer?: string }
function engine(on: any, files: Files) {
  const compacted: string[] = []
  const commands: string[] = []
  const logs: string[] = []
  const reads: string[] = []
  mock.env(on, files.env ?? {})
  const clock = mock.clock(on)
  on('tool.call', () => ({ result: { stdout: files.stdout ?? DONE_OUT, stderr: '', interrupted: false } }))
  on('fs.read', (_: unknown, e: any) => {
    reads.push(e.path)
    if (/[\\/]plan-sessions[\\/]/.test(e.path)) {
      return files.pointer === undefined ? { deny: 'ENOENT' } : { value: files.pointer }
    }
    return { value: files.plan }
  })
  on('session.id', () => ({ value: 's1' }))
  on('process.run', () => ({ value: { exitCode: 0, stdout: files.git, stderr: '' } }))
  on('session.usage', () => ({ value: { startedAt: 0, context: { tokens: files.tokens, window: 1_000_000 }, rateLimits: [] } }))
  on('turn.complete', (_: unknown, e: any) => ({ text: e.answer }))
  on('command.run', (_: unknown, e: any) => {
    commands.push(`/${e.command} ${e.args}`)
    return { text: '' }
  })
  on('session.compact', (_: unknown, e: any) => {
    if (files.headless) return { skip: 'HooksError: $.session.compact: not available in a headless (-p / SDK) session yet' }
    compacted.push(e.instructions)
    return { messages: [{ role: 'user', text: 'summary', toolUses: [] }] }
  })
  on('ui.status', () => ({ value: undefined }))
  on('ui.log', (_: unknown, e: any) => {
    logs.push(e.text)
    return { value: undefined }
  })
  // The test engine resolves a path as the host does (a drive letter and backslashes on Windows).
  const norm = (paths: string[]) => paths.map(p => p.replaceAll('\\', '/').replace(/^[A-Za-z]:/, ''))
  return { compacted, commands, logs, clock, get reads() { return norm(reads) } }
}

const TURN = { turnId: 't1', reason: 'answer', answer: 'ok', durationMs: 1, isAborted: false }
const OPTS = { options: { compact_on_stage_close: true } }
const WITH_ROOT = { options: { compact_on_stage_close: true, plans_root: ROOT } }
const CLOSE = { tool: 'Bash', command: 'work-plan done 2 --result r --proof p' } as any

test('a closed, committed stage compacts the conversation at the turn end', OPTS, async ($, on) => {
  const seen = engine(on, { plan: plan([done2, next3]), git: '', tokens: 400_000 })
  await $.tool.call(CLOSE)
  await $.turn.complete(TURN as any)
  await seen.clock.settle()
  expect(seen.reads).toEqual([PLAN])
  expect(seen.compacted.length).toBe(1)
  expect(seen.compacted[0]).toContain('stage 3 (Proof: live)')
  expect(seen.logs.at(-1)).toContain('Context compacted after stage 2 (400K tokens); next: 3. Proof: live')
})

test('with the option off (the default) nothing compacts', async ($, on) => {
  const seen = engine(on, { plan: plan([done2, next3]), git: '', tokens: 400_000 })
  await $.tool.call(CLOSE)
  await $.turn.complete(TURN as any)
  await seen.clock.settle()
  expect(seen.compacted.length).toBe(0)
  expect(seen.logs).toEqual([])
})

test('a relative label resolves under the plans root option', WITH_ROOT, async ($, on) => {
  const seen = engine(on, { plan: plan([done2, next3]), git: '', tokens: 400_000, stdout: REL_OUT })
  await $.tool.call(CLOSE)
  await $.turn.complete(TURN as any)
  await seen.clock.settle()
  expect(seen.reads).toEqual([REL_PLAN])
  expect(seen.compacted.length).toBe(1)
})

test('a done piped through tail compacts from the session pointer', WITH_ROOT, async ($, on) => {
  const seen = engine(on, { plan: plan([done2, next3]), git: '', tokens: 400_000, stdout: '  next: stage 3, Proof: live', pointer: `${PLAN}\n` })
  await $.tool.call({ tool: 'Bash', command: 'work-plan done 2 --result r --proof p 2>&1 | tail -1' } as any)
  await $.turn.complete(TURN as any)
  await seen.clock.settle()
  expect(seen.reads).toEqual([`${ROOT}/.claude/plan-sessions/s1`, PLAN])
  expect(seen.compacted.length).toBe(1)
})

test('a piped done with no plans root is no compaction', OPTS, async ($, on) => {
  const seen = engine(on, { plan: plan([done2, next3]), git: '', tokens: 400_000, stdout: '  next: stage 3, Proof: live' })
  await $.tool.call({ tool: 'Bash', command: 'work-plan done 2 --result r --proof p | tail -1' } as any)
  await $.turn.complete(TURN as any)
  await seen.clock.settle()
  expect(seen.compacted.length).toBe(0)
  expect(seen.logs).toEqual([])
})

test('a plan that cannot be read names the path it tried', OPTS, async ($, on) => {
  const seen = engine(on, { plan: 'not json', git: '', tokens: 400_000 })
  await $.tool.call(CLOSE)
  await $.turn.complete(TURN as any)
  await seen.clock.settle()
  expect(seen.logs.at(-1)).toContain(`skipped: the plan file is gone or unreadable: ${PLAN}`)
})

test('an uncommitted change defers, and the commit in the next turn lets it compact', OPTS, async ($, on) => {
  const files = { plan: plan([done2, next3]), git: ' M a.txt\n', tokens: 400_000 }
  const seen = engine(on, files)
  await $.tool.call(CLOSE)
  await $.turn.complete(TURN as any)
  await seen.clock.settle()
  expect(seen.compacted.length).toBe(0)
  expect(seen.logs.at(-1)).toContain('deferred: the repository has uncommitted changes')
  files.git = ''
  await $.turn.complete({ ...TURN, turnId: 't2' } as any)
  await seen.clock.settle()
  expect(seen.compacted.length).toBe(1)
})

test('no compaction once the next stage started in the same turn', OPTS, async ($, on) => {
  const seen = engine(on, { plan: plan([done2, { ...next3, status: 'in_progress' }]), git: '', tokens: 400_000 })
  await $.tool.call(CLOSE)
  await $.turn.complete(TURN as any)
  await seen.clock.settle()
  expect(seen.compacted.length).toBe(0)
  expect(seen.logs.at(-1)).toContain('skipped: stage 3 is already in progress')
})

test('no compaction for an interrupted turn, a subagent, or a turn without a stage close', OPTS, async ($, on) => {
  const seen = engine(on, { plan: plan([done2, next3]), git: '', tokens: 400_000 })
  await $.turn.complete(TURN as any)
  await $.tool.call({ ...CLOSE, agentId: 'a1' })
  await $.turn.complete(TURN as any)
  await $.tool.call(CLOSE)
  await $.turn.complete({ ...TURN, reason: 'aborted', isAborted: true } as any)
  await $.turn.complete({ ...TURN, agentId: 'a1' } as any)
  await seen.clock.settle()
  expect(seen.compacted.length).toBe(0)
})

test('a failed done is no stage close', OPTS, async ($, on) => {
  const seen = engine(on, { plan: plan([done2, next3]), git: '', tokens: 400_000, stdout: 'work_plan: done needs --result and --proof' })
  await $.tool.call({ tool: 'Bash', command: 'work-plan done 2' } as any)
  await $.turn.complete(TURN as any)
  await seen.clock.settle()
  expect(seen.compacted.length).toBe(0)
})

test('an SDK host runs /compact with the same instructions', OPTS, async ($, on) => {
  const seen = engine(on, { plan: plan([done2, next3]), git: '', tokens: 400_000, headless: true })
  await $.tool.call(CLOSE)
  await $.turn.complete(TURN as any)
  await seen.clock.settle()
  expect(seen.commands.length).toBe(1)
  expect(seen.commands[0]).toContain('/compact Stage 2 of the active plan')
  expect(seen.logs.at(-1)).toContain('Compaction requested after stage 2 (/compact); next: 3. Proof: live')
})
