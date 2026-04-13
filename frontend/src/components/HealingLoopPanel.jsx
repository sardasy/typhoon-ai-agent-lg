import {
  WrenchScrewdriverIcon,
  ExclamationTriangleIcon,
  CheckCircleIcon,
  XCircleIcon,
  ArrowPathIcon
} from '@heroicons/react/24/outline'

const CATEGORY_COLORS = {
  firmware: 'bg-purple-100 dark:bg-purple-500/10 text-purple-700 dark:text-purple-400 border-purple-200 dark:border-purple-500/30',
  hardware: 'bg-red-100 dark:bg-red-500/10 text-red-700 dark:text-red-400 border-red-200 dark:border-red-500/30',
  tuning:   'bg-amber-100 dark:bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-200 dark:border-amber-500/30',
  wiring:   'bg-orange-100 dark:bg-orange-500/10 text-orange-700 dark:text-orange-400 border-orange-200 dark:border-orange-500/30',
}

function ConfidenceBar({ confidence }) {
  const pct = Math.round(confidence * 100)
  const color = pct >= 80 ? 'bg-emerald-500' : pct >= 50 ? 'bg-amber-500' : 'bg-red-500'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full transition-all duration-500`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-mono text-gray-500 w-10 text-right">{pct}%</span>
    </div>
  )
}

export default function HealingLoopPanel({ healingHistory }) {
  const totalAttempts = healingHistory.length
  const healed = healingHistory.filter((h) => h.outcome === 'pass').length
  const escalated = healingHistory.filter((h) => h.outcome === 'escalate' || h.outcome === 'fail').length
  const avgConfidence = totalAttempts > 0
    ? healingHistory.reduce((sum, h) => sum + (h.diagnosis?.confidence ?? 0), 0) / totalAttempts
    : 0

  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl shadow-xl overflow-hidden">
      <div className="px-5 py-3.5 border-b border-gray-200 dark:border-gray-800 flex items-center gap-2">
        <ArrowPathIcon className="w-4 h-4 text-amber-500" />
        <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-200 tracking-tight">Self-Healing Loop</h2>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-3 gap-4 px-5 py-4 border-b border-gray-100 dark:border-gray-800/50">
        <div>
          <p className="text-xs text-gray-500 uppercase tracking-wider">Attempts</p>
          <p className="text-2xl font-semibold text-gray-800 dark:text-gray-200 mt-1">{totalAttempts}</p>
        </div>
        <div>
          <p className="text-xs text-gray-500 uppercase tracking-wider">Healed</p>
          <p className="text-2xl font-semibold text-emerald-600 dark:text-emerald-400 mt-1">{healed}</p>
        </div>
        <div>
          <p className="text-xs text-gray-500 uppercase tracking-wider">Avg Confidence</p>
          <p className="text-2xl font-semibold text-amber-600 dark:text-amber-400 mt-1">{Math.round(avgConfidence * 100)}%</p>
        </div>
      </div>

      {/* Timeline */}
      <div className="px-5 py-4 space-y-4 max-h-[500px] overflow-y-auto">
        {totalAttempts === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 gap-2">
            <WrenchScrewdriverIcon className="w-8 h-8 text-gray-300 dark:text-gray-700" />
            <p className="text-sm text-gray-400 dark:text-gray-600">No healing attempts yet</p>
          </div>
        ) : (
          healingHistory.map((entry, i) => {
            const d = entry.diagnosis || {}
            const catClass = CATEGORY_COLORS[d.root_cause_category] || CATEGORY_COLORS.firmware
            return (
              <div key={i} className="relative pl-6 border-l-2 border-amber-300 dark:border-amber-500/40">
                {/* Timeline dot */}
                <div className="absolute -left-[9px] top-1 w-4 h-4 rounded-full bg-amber-100 dark:bg-amber-500/20 border-2 border-amber-400 dark:border-amber-500 flex items-center justify-center">
                  <ExclamationTriangleIcon className="w-2.5 h-2.5 text-amber-600 dark:text-amber-400" />
                </div>

                <div className="space-y-2">
                  {/* Header */}
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium text-gray-800 dark:text-gray-200">
                      Attempt #{i + 1}
                    </span>
                    {d.failed_scenario_id && (
                      <span className="text-xs text-gray-500 font-mono">{d.failed_scenario_id}</span>
                    )}
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium border ${catClass}`}>
                      {d.root_cause_category || 'unknown'}
                    </span>
                    {entry.outcome === 'pass' && (
                      <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400 font-medium">
                        <CheckCircleIcon className="w-3.5 h-3.5" /> Healed
                      </span>
                    )}
                    {(entry.outcome === 'fail' || entry.outcome === 'escalate') && (
                      <span className="flex items-center gap-1 text-xs text-red-600 dark:text-red-400 font-medium">
                        <XCircleIcon className="w-3.5 h-3.5" /> Escalated
                      </span>
                    )}
                  </div>

                  {/* Root cause */}
                  {d.root_cause_description && (
                    <p className="text-xs text-gray-600 dark:text-gray-400">{d.root_cause_description}</p>
                  )}

                  {/* Confidence */}
                  {d.confidence != null && (
                    <div>
                      <span className="text-xs text-gray-500 mb-1 block">Confidence</span>
                      <ConfidenceBar confidence={d.confidence} />
                    </div>
                  )}

                  {/* Evidence */}
                  {d.evidence && d.evidence.length > 0 && (
                    <div>
                      <span className="text-xs text-gray-500 mb-1 block">Evidence</span>
                      <ul className="space-y-0.5">
                        {d.evidence.map((e, j) => (
                          <li key={j} className="text-xs text-gray-600 dark:text-gray-400 flex items-start gap-1.5">
                            <span className="w-1 h-1 rounded-full bg-gray-400 mt-1.5 flex-shrink-0" />
                            {e}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Fix applied */}
                  {entry.fix && (
                    <div className="px-3 py-2 rounded bg-indigo-50 dark:bg-indigo-500/10 border border-indigo-200 dark:border-indigo-500/20">
                      <span className="text-xs text-indigo-600 dark:text-indigo-400 font-medium">Corrective Action</span>
                      <p className="text-xs font-mono text-indigo-700 dark:text-indigo-300 mt-0.5">
                        XCP write: {entry.fix.param || d.corrective_param} = {entry.fix.new_value ?? d.corrective_value}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
