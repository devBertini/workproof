// Entry of proofguard's hooks module: each mod runs only when its module option allows it.
// `options` holds the plugin's userConfig values with the manifest defaults filled in; a change reloads the module.
import type { Register } from 'claude-code'
import { afterTurn as compactAfterTurn, register as stageCompact } from './stage-compact'
import { afterTurn as watchAfterTurn, register as watchRun } from './watch-run'

export const register: Register = (on, options) => {
  const longRuns = options.long_runs !== false
  const compact = options.proof_plans !== false && options.compact_on_stage_close === true
  if (!longRuns && !compact) return
  if (longRuns) watchRun(on, options)
  if (compact) stageCompact(on, options)
  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    try {
      if (longRuns) watchAfterTurn($)
      if (compact) await compactAfterTurn($, e)
    } catch {
      // A follow-up that fails must not change the turn's own result.
    }
    return done
  })
}
