import { useState } from 'react'
import { CheckCircleIcon, XCircleIcon, ClockIcon, ExclamationCircleIcon, ChevronDownIcon } from '@heroicons/react/20/solid'

const STATUS = {
  pass:    { icon: CheckCircleIcon,      label: 'PASS',    badge: 'bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400' },
  fail:    { icon: XCircleIcon,          label: 'FAIL',    badge: 'bg-red-500/10 border-red-500/30 text-red-600 dark:text-red-400' },
  error:   { icon: ExclamationCircleIcon, label: 'ERROR',  badge: 'bg-orange-500/10 border-orange-500/30 text-orange-600 dark:text-orange-400' },
  pending: { icon: ClockIcon,            label: 'PENDING', badge: 'bg-gray-200 dark:bg-gray-700/50 border-gray-300 dark:border-gray-700 text-gray-500' }
}

export default function ScenarioTable({ scenarios, selectedId, onSelect }) {
  const [expandedIdx, setExpandedIdx] = useState(null)

  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl shadow-xl overflow-hidden">
      <div className="px-5 py-3.5 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-200 tracking-tight">Scenario Results</h2>
        <span className="text-xs text-gray-400 dark:text-gray-600">{scenarios.length} scenarios</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 dark:border-gray-800">
              <th className="text-left px-5 py-3 text-xs font-medium text-gray-500 dark:text-gray-600 uppercase tracking-wider">Scenario</th>
              <th className="text-left px-5 py-3 text-xs font-medium text-gray-500 dark:text-gray-600 uppercase tracking-wider">Type</th>
              <th className="text-left px-5 py-3 text-xs font-medium text-gray-500 dark:text-gray-600 uppercase tracking-wider">Status</th>
              <th className="text-right px-5 py-3 text-xs font-medium text-gray-500 dark:text-gray-600 uppercase tracking-wider">Attempts</th>
              <th className="text-right px-5 py-3 text-xs font-medium text-gray-500 dark:text-gray-600 uppercase tracking-wider">Duration</th>
              <th className="w-8"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-800/60">
            {scenarios.length === 0 ? (
              <tr>
                <td colSpan={6} className="text-center py-14 text-gray-400 dark:text-gray-600">
                  No scenarios yet. Enter a goal and click Run.
                </td>
              </tr>
            ) : (
              scenarios.map((s, i) => {
                const cfg = STATUS[s.status] ?? STATUS.pending
                const Icon = cfg.icon
                const isSelected = selectedId === s.name
                const isExpanded = expandedIdx === i
                return (
                  <tr key={i} className="group">
                    <td colSpan={6} className="p-0">
                      <div
                        className={`flex items-center cursor-pointer px-5 py-3.5 transition-colors ${
                          isSelected
                            ? 'bg-indigo-50 dark:bg-indigo-500/10'
                            : 'hover:bg-gray-50 dark:hover:bg-gray-800/30'
                        }`}
                        onClick={() => onSelect?.(isSelected ? null : s.name)}
                      >
                        <span className="flex-1 font-medium text-gray-800 dark:text-gray-200 min-w-[200px]">{s.name}</span>
                        <span className="w-32 text-gray-500">{s.type}</span>
                        <span className="w-28">
                          <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border ${cfg.badge}`}>
                            <Icon className="w-3 h-3" />
                            {cfg.label}
                          </span>
                        </span>
                        <span className="w-20 text-right text-gray-500 tabular-nums">{s.attempts}</span>
                        <span className="w-20 text-right text-gray-500 tabular-nums font-mono">{s.duration}</span>
                        <button
                          onClick={(e) => { e.stopPropagation(); setExpandedIdx(isExpanded ? null : i) }}
                          className="ml-2 p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
                        >
                          <ChevronDownIcon className={`w-4 h-4 text-gray-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
                        </button>
                      </div>
                      {isExpanded && (
                        <div className="px-5 pb-4 pt-1 bg-gray-50 dark:bg-gray-800/40 border-t border-gray-100 dark:border-gray-800/50">
                          <div className="grid grid-cols-2 gap-4 text-xs">
                            {s.fail_reason && (
                              <div>
                                <span className="text-gray-500 font-medium">Fail Reason:</span>
                                <p className="text-red-600 dark:text-red-400 mt-0.5">{s.fail_reason}</p>
                              </div>
                            )}
                            {s.root_cause && (
                              <div>
                                <span className="text-gray-500 font-medium">Root Cause:</span>
                                <p className="text-amber-600 dark:text-amber-400 mt-0.5">{s.root_cause}</p>
                              </div>
                            )}
                            {s.corrective_action && (
                              <div>
                                <span className="text-gray-500 font-medium">Corrective Action:</span>
                                <p className="text-indigo-600 dark:text-indigo-400 mt-0.5 font-mono">{s.corrective_action}</p>
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
