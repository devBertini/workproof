// Entry of workproof's hooks module: each mod runs only when its module option allows it.
// `options` holds the plugin's userConfig values with the manifest defaults filled in; a change reloads the module.
// Plugin options live only in user settings, so compaction can also be turned on per project with the environment
// variable WORKPROOF_COMPACT_ON_STAGE_CLOSE=on (the `env` block of any settings file).
import type { Register } from 'claude-code'
import { afterTurn as compactAfterTurn, register as stageCompact } from './stage-compact'
import { afterTurn as watchAfterTurn, register as watchRun } from './watch-run'

const ON = new Set(['true', '1', 'yes', 'on'])

export const register: Register = (on, options) => {
  const longRuns = options.long_runs !== false
  const plans = options.proof_plans !== false
  const compactOption = options.compact_on_stage_close === true
  if (!longRuns && !plans) return
  if (longRuns) watchRun(on, options)
  if (plans) stageCompact(on, options)
  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    try {
      if (longRuns) watchAfterTurn($)
      if (plans) {
        const fromEnv = ON.has(String((await $.env.get('WORKPROOF_COMPACT_ON_STAGE_CLOSE')) ?? '').trim().toLowerCase())
        if (compactOption || fromEnv) await compactAfterTurn($, e)
      }
    } catch {
      // A follow-up that fails must not change the turn's own result.
    }
    return done
  })
}
