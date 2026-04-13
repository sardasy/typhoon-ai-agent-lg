import { useMemo } from 'react'
import { ChartBarIcon } from '@heroicons/react/24/outline'

// Lightweight waveform stats visualization without external chart library
// Uses inline SVG bar charts for signal statistics

const STAT_KEYS = ['mean', 'max', 'min', 'rms']
const STAT_COLORS = {
  mean: { bar: 'bg-sky-500',     text: 'text-sky-600 dark:text-sky-400' },
  max:  { bar: 'bg-red-500',     text: 'text-red-600 dark:text-red-400' },
  min:  { bar: 'bg-emerald-500', text: 'text-emerald-600 dark:text-emerald-400' },
  rms:  { bar: 'bg-indigo-500',  text: 'text-indigo-600 dark:text-indigo-400' },
}

const TIMING_KEYS = [
  { key: 'overshoot_percent', label: 'Overshoot', unit: '%',  color: 'text-amber-600 dark:text-amber-400', bar: 'bg-amber-500' },
  { key: 'rise_time_ms',      label: 'Rise Time', unit: 'ms', color: 'text-sky-600 dark:text-sky-400',     bar: 'bg-sky-500' },
  { key: 'settling_time_ms',  label: 'Settling',  unit: 'ms', color: 'text-indigo-600 dark:text-indigo-400', bar: 'bg-indigo-500' },
]

function MiniBar({ value, maxValue, colorClass }) {
  const pct = maxValue > 0 ? Math.min((value / maxValue) * 100, 100) : 0
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-3 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div
          className={`h-full ${colorClass} rounded-full transition-all duration-300`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs font-mono text-gray-500 w-16 text-right">{typeof value === 'number' ? value.toFixed(2) : '-'}</span>
    </div>
  )
}

export default function WaveformCharts({ scenarioResults, selectedScenarioId, onSelectScenario }) {
  // Collect all waveform stats, optionally filtered
  const allStats = useMemo(() => {
    const results = selectedScenarioId
      ? scenarioResults.filter((r) => r.scenario_id === selectedScenarioId)
      : scenarioResults
    return results.flatMap((r) =>
      (r.waveform_stats || []).map((ws) => ({ ...ws, scenario_id: r.scenario_id }))
    )
  }, [scenarioResults, selectedScenarioId])

  // Get unique scenario IDs for selection
  const scenarioIds = useMemo(() => {
    return [...new Set(scenarioResults.map((r) => r.scenario_id).filter(Boolean))]
  }, [scenarioResults])

  // Find max values for scaling bars
  const maxStat = useMemo(() => {
    if (allStats.length === 0) return 1
    return Math.max(...allStats.map((s) => Math.max(Math.abs(s.mean || 0), Math.abs(s.max || 0), Math.abs(s.min || 0), Math.abs(s.rms || 0), 1)))
  }, [allStats])

  const maxTiming = useMemo(() => {
    if (allStats.length === 0) return 1
    return Math.max(...allStats.map((s) => Math.max(s.rise_time_ms || 0, s.settling_time_ms || 0, 1)))
  }, [allStats])

  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl shadow-xl overflow-hidden">
      <div className="px-5 py-3.5 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ChartBarIcon className="w-4 h-4 text-indigo-500" />
          <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-200 tracking-tight">Waveform Analysis</h2>
        </div>

        {/* Scenario filter */}
        {scenarioIds.length > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-500">Filter:</span>
            <select
              value={selectedScenarioId || ''}
              onChange={(e) => onSelectScenario?.(e.target.value || null)}
              className="text-xs bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded px-2 py-1 text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            >
              <option value="">All scenarios</option>
              {scenarioIds.map((id) => (
                <option key={id} value={id}>{id}</option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div className="p-5">
        {allStats.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 gap-2">
            <ChartBarIcon className="w-8 h-8 text-gray-300 dark:text-gray-700" />
            <p className="text-sm text-gray-400 dark:text-gray-600">No waveform data available</p>
            <p className="text-xs text-gray-400 dark:text-gray-600">Run a test to see signal statistics</p>
          </div>
        ) : (
          <div className="space-y-6">
            {allStats.map((ws, i) => (
              <div key={`${ws.scenario_id}-${ws.signal}-${i}`} className="space-y-3">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-medium text-gray-800 dark:text-gray-200">{ws.signal}</h3>
                  <span className="text-xs text-gray-400 font-mono">{ws.scenario_id}</span>
                </div>

                {/* Signal statistics bars */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2">
                  {STAT_KEYS.map((key) => (
                    <div key={key} className="flex items-center gap-3">
                      <span className={`text-xs font-medium w-10 ${STAT_COLORS[key].text}`}>{key.toUpperCase()}</span>
                      <MiniBar value={ws[key] ?? 0} maxValue={maxStat} colorClass={STAT_COLORS[key].bar} />
                    </div>
                  ))}
                </div>

                {/* Timing metrics */}
                <div className="grid grid-cols-3 gap-3 mt-2">
                  {TIMING_KEYS.map(({ key, label, unit, color, bar }) => {
                    const val = ws[key]
                    if (val == null) return null
                    return (
                      <div key={key} className="bg-gray-50 dark:bg-gray-800/50 rounded-lg px-3 py-2">
                        <p className="text-xs text-gray-500">{label}</p>
                        <p className={`text-lg font-semibold ${color} tabular-nums`}>
                          {typeof val === 'number' ? val.toFixed(1) : '-'}
                          <span className="text-xs font-normal text-gray-400 ml-0.5">{unit}</span>
                        </p>
                      </div>
                    )
                  })}
                </div>

                {i < allStats.length - 1 && <hr className="border-gray-100 dark:border-gray-800" />}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
