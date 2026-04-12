import { CheckCircleIcon, XCircleIcon, ClockIcon, ExclamationCircleIcon } from '@heroicons/react/20/solid'

const STATUS = {
  pass:    { icon: CheckCircleIcon,      label: 'PASS',    text: 'text-emerald-400', badge: 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400' },
  fail:    { icon: XCircleIcon,          label: 'FAIL',    text: 'text-red-400',     badge: 'bg-red-500/10 border-red-500/30 text-red-400' },
  error:   { icon: ExclamationCircleIcon, label: 'ERROR',  text: 'text-orange-400',  badge: 'bg-orange-500/10 border-orange-500/30 text-orange-400' },
  pending: { icon: ClockIcon,            label: 'PENDING', text: 'text-gray-500',    badge: 'bg-gray-700/50 border-gray-700 text-gray-500' }
}

export default function ScenarioTable({ scenarios }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl shadow-xl overflow-hidden">
      <div className="px-5 py-3.5 border-b border-gray-800 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-200 tracking-tight">Scenario Results</h2>
        <span className="text-xs text-gray-600">{scenarios.length} scenarios</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800">
              <th className="text-left px-5 py-3 text-xs font-medium text-gray-600 uppercase tracking-wider">Scenario</th>
              <th className="text-left px-5 py-3 text-xs font-medium text-gray-600 uppercase tracking-wider">Type</th>
              <th className="text-left px-5 py-3 text-xs font-medium text-gray-600 uppercase tracking-wider">Status</th>
              <th className="text-right px-5 py-3 text-xs font-medium text-gray-600 uppercase tracking-wider">Attempts</th>
              <th className="text-right px-5 py-3 text-xs font-medium text-gray-600 uppercase tracking-wider">Duration</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/60">
            {scenarios.length === 0 ? (
              <tr>
                <td colSpan={5} className="text-center py-14 text-gray-600">
                  No scenarios yet. Enter a goal and click Run.
                </td>
              </tr>
            ) : (
              scenarios.map((s, i) => {
                const cfg = STATUS[s.status] ?? STATUS.pending
                const Icon = cfg.icon
                return (
                  <tr key={i} className="hover:bg-gray-800/30 transition-colors">
                    <td className="px-5 py-3.5 font-medium text-gray-200">{s.name}</td>
                    <td className="px-5 py-3.5 text-gray-500">{s.type}</td>
                    <td className="px-5 py-3.5">
                      <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border ${cfg.badge}`}>
                        <Icon className="w-3 h-3" />
                        {cfg.label}
                      </span>
                    </td>
                    <td className="px-5 py-3.5 text-right text-gray-500 tabular-nums">{s.attempts}</td>
                    <td className="px-5 py-3.5 text-right text-gray-500 tabular-nums font-mono">{s.duration}</td>
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
