// Entry of workproof's hooks module: each mod runs only when its module option allows it.
// `options` holds the plugin's userConfig values with the manifest defaults filled in; a change reloads the module.
// Plugin options live only in user settings, so compaction can also be turned on per project with the environment
// variable WORKPROOF_COMPACT_ON_STAGE_CLOSE=on (the `env` block of any settings file).
import type { Register } from 'claude-code'
import { afterTurn as compactAfterTurn, compactionOn, register as stageCompact } from './stage-compact'
import { runSelfTest } from './self-test'
import { afterTurn as watchAfterTurn, registerTool, register as watchRun } from './watch-run'

export const register: Register = (on, options) => {
  const longRuns = options.long_runs !== false
  const plans = options.proof_plans !== false
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    if (longRuns) await registerTool($)
    runSelfTest($)
    return started
  })
  if (!longRuns && !plans) return
  if (longRuns) watchRun(on, options)
  if (plans) stageCompact(on, options)
  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    try {
      if (longRuns) watchAfterTurn($)
      if (plans && (await compactionOn($, options.compact_on_stage_close))) await compactAfterTurn($, e)
    } catch {
      // A follow-up that fails must not change the turn's own result.
    }
    return done
  })
}
