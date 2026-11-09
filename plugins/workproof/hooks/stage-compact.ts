// Stage-close compaction (module proof_plans, option compact_on_stage_close, off by default).
// After `work-plan done N` closes a stage in the main session, the conversation is compacted at the end of that
// turn, and only when the stage is complete on disk: stage N is done with its result and proof, no stage is in
// progress, a next stage is pending, and the plan's repository has nothing uncommitted. There is no floor on the
// context size: auto-compaction still covers a stage that outgrows the window. Otherwise nothing happens; an
// uncommitted change only defers the compaction to the next turn end. Each outcome is one line in the transcript.
// Where the host cannot compact between turns (an SDK or headless host) it runs /compact with the same
// instructions instead. After the compaction the SessionStart hook prints the plan, whose stages and notes are the
// record of the work.
//
// Which plan file a close belongs to: the label in the command's output (`ACTIVE PLAN <label>: ...`), an absolute
// path when no plans root is set, else a path from the plans root; failing that (output piped through tail or
// grep) the session's pointer file, which exists only when a plans root is set. Without either, nothing is done.
import type { EngineInterface, Register } from 'claude-code'
import { setStatus } from './status'

const ARG = String.raw`(?:"[^"]*"|'[^']*'|\S+)`
const DONE = new RegExp(String.raw`\bwork[-_]plan(?:\.py)?\s+(?:--where\s+${ARG}\s+)?done\s+(\d+)\b`)
const LABEL = /^ACTIVE PLAN (.+?): \d+ done/m
const HEADLESS = /not available in a headless/
const ABSOLUTE = /^(\/|[A-Za-z]:\/)/
const ON = new Set(['true', '1', 'yes', 'on'])
const STATUS = 'stage-compact'

type Stage = { id: number; title: string; status: string; result?: string; proof?: string; notes?: string[] }
type Plan = { goal: string; stages: Stage[] }
export type Closed = { plan: string; stage: number }
export type Verdict =
  | { act: 'compact'; next: Stage }
  | { act: 'defer'; why: string }
  | { act: 'forget'; why: string }

let closed: Closed | undefined

// Whether compaction is on: the plugin option, or WORKPROOF_COMPACT_ON_STAGE_CLOSE (plugin options live only in user
// settings; the variable turns it on from the `env` block of any settings file).
export async function compactionOn($: EngineInterface, option: unknown): Promise<boolean> {
  if (option === true) return true
  const value = await $.env.get('WORKPROOF_COMPACT_ON_STAGE_CLOSE').catch(() => undefined)
  return ON.has(String(value ?? '').trim().toLowerCase())
}

// The stage a `work-plan done` closed and its plan file; `root` is the plans root ('' when none is set).
// Whether the stage really closed is read from the plan on disk at the turn end (verdictOf).
export function closedBy(command: string, stdout: string, root: string, pointer?: string): Closed | undefined {
  const done = DONE.exec(command)
  if (done === null) return undefined
  const stage = Number(done[1])
  const label = LABEL.exec(stdout)
  if (label !== null && !new RegExp(`^\\s+\\[done\\] ${stage}\\. `, 'm').test(stdout)) return undefined
  const plan = (label?.[1] ?? pointer?.trim() ?? '').replaceAll('\\', '/')
  if (plan === '') return undefined
  if (ABSOLUTE.test(plan)) return { plan, stage }
  return root === '' ? undefined : { plan: `${root.replace(/\/+$/, '')}/${plan}`, stage }
}

// The plans root as work-plan reads it: the environment variable, else the plugin option, else none.
export function rootOf(option: unknown, env: string | undefined): string {
  const value = env !== undefined && env.trim() !== '' ? env : typeof option === 'string' ? option : ''
  return value.trim().replaceAll('\\', '/')
}

// Whether the closed stage leaves the session ready to be compacted, from the plan as it is on disk now.
export function verdictOf(planText: string | undefined, stage: number, gitStatus: string | undefined, path = ''): Verdict {
  let plan: Plan
  try {
    plan = JSON.parse(planText ?? '') as Plan
  } catch {
    return { act: 'forget', why: `the plan file is gone or unreadable${path === '' ? '' : `: ${path}`}` }
  }
  const own = plan.stages?.find(s => s.id === stage)
  if (own === undefined || own.status !== 'done' || !own.result || !own.proof) {
    return { act: 'forget', why: `stage ${stage} is not done with its result and proof` }
  }
  const running = plan.stages.find(s => s.status === 'in_progress')
  if (running !== undefined) return { act: 'forget', why: `stage ${running.id} is already in progress` }
  const next = plan.stages.find(s => s.status === 'pending')
  if (next === undefined) return { act: 'forget', why: 'no stage is left to do' }
  if (gitStatus === undefined) return { act: 'forget', why: 'git status failed' }
  if (gitStatus.trim() !== '') return { act: 'defer', why: 'the repository has uncommitted changes' }
  return { act: 'compact', next }
}

export function instructionsOf(closed: Closed, next: Stage): string {
  return [
    `Stage ${closed.stage} of the active plan ${closed.plan} is done, proven and committed.`,
    `Keep: the user's request and every decision or open question of the user; stage ${next.id} (${next.title}), which comes next, and what the session learned that it needs and the plan's notes do not hold.`,
    'Drop: the tool output and the step-by-step of the closed stage; the plan file, its notes and the git log are their record.',
  ].join(' ')
}

function dirOf(path: string): string {
  return path.slice(0, path.lastIndexOf('/'))
}

async function check($: EngineInterface, seen: Closed): Promise<Verdict> {
  const text = await $.fs.read(seen.plan).catch(() => undefined)
  const git = await $.process.run(['git', '-C', dirOf(seen.plan), 'status', '--porcelain'])
    .then(r => (r.exitCode === 0 ? r.stdout : undefined))
    .catch(() => undefined)
  return verdictOf(typeof text === 'string' ? text : undefined, seen.stage, git, seen.plan)
}

async function compact($: EngineInterface, seen: Closed, next: Stage): Promise<void> {
  const tokens = (await $.session.usage()).context.tokens
  const instructions = instructionsOf(seen, next)
  const done = await $.session.compact({ instructions })
    .catch((error: unknown) => ({ skip: String(error) }))
  if (done.skip !== undefined && HEADLESS.test(done.skip)) {
    // An SDK host compacts only inside a turn: run /compact as if the person typed it.
    const ran = await $.command.run({ command: 'compact', args: instructions })
      .then(() => undefined, (error: unknown) => String(error))
    $.ui.log(ran === undefined
      ? `Compaction requested after stage ${seen.stage} (/compact); next: ${next.id}. ${next.title}`
      : `Compaction after stage ${seen.stage} not done: ${ran}`)
    return
  }
  if (done.skip !== undefined) {
    $.ui.log(`Compaction after stage ${seen.stage} not done: ${done.skip}`)
    return
  }
  const size = tokens === undefined ? '' : ` (${Math.round(tokens / 1000)}K tokens)`
  $.ui.log(`Context compacted after stage ${seen.stage}${size}; next: ${next.id}. ${next.title}`)
}

// At a main-session turn that answered: compact if the stage closed in it left the session ready.
export async function afterTurn($: EngineInterface, e: { agentId?: string; reason: string }): Promise<void> {
  const seen = closed
  if (seen === undefined || e.agentId !== undefined || e.reason !== 'answer') return
  const verdict = await check($, seen)
  setStatus($, STATUS, undefined)
  if (verdict.act === 'defer') {
    $.ui.log(`Compaction after stage ${seen.stage} deferred: ${verdict.why}`)
    return
  }
  closed = undefined
  if (verdict.act === 'forget') {
    $.ui.log(`Compaction after stage ${seen.stage} skipped: ${verdict.why}`)
    return
  }
  $.clock.after(0, () => void compact($, seen, verdict.next))
}

export const register: Register = (on, options) => {
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const ran = await next(e)
    if (e.agentId !== undefined || !DONE.test(e.command) || ran.deny !== undefined || ran.isError === true) return ran
    try {
      if (!(await compactionOn($, options.compact_on_stage_close))) return ran
      const stdout = (ran.result as { stdout?: unknown } | undefined)?.stdout
      if (typeof stdout !== 'string') return ran
      // PROOF_PLANS_ROOT is the variable's name from before the plugin was renamed: still read.
      const root = rootOf(options.plans_root, (await $.env.get('WORKPROOF_PLANS_ROOT')) || (await $.env.get('PROOF_PLANS_ROOT')))
      let found = closedBy(e.command, stdout, root)
      if (found === undefined && !LABEL.test(stdout) && root !== '') {
        const pointer = await $.fs.read(`${root}/.claude/plan-sessions/${await $.session.id()}`).catch(() => undefined)
        found = closedBy(e.command, stdout, root, typeof pointer === 'string' ? pointer : undefined)
      }
      if (found !== undefined) {
        closed = found
        setStatus($, STATUS, `stage ${found.stage} closed: compaction at the end of the turn, if everything is committed`)
      }
    } catch {
      // A stage close this hook cannot read is no compaction; the call's own result stands.
    }
    return ran
  }).catch(($, e, next) => (next.called ? { deny: 'workproof: the stage-close hook failed after the command ran; read its output again.' } : next(e)))
}
