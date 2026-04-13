import {
  ListBulletIcon,
  CheckCircleIcon,
  XCircleIcon,
  ChartBarIcon
} from '@heroicons/react/24/outline'

const CARDS = [
  {
    key: 'total',
    label: 'Total Scenarios',
    icon: ListBulletIcon,
    text: 'text-sky-500 dark:text-sky-400',
    bg: 'bg-sky-500/10',
    border: 'border-sky-500/20',
    glow: 'shadow-sky-500/5'
  },
  {
    key: 'passed',
    label: 'Passed',
    icon: CheckCircleIcon,
    text: 'text-emerald-500 dark:text-emerald-400',
    bg: 'bg-emerald-500/10',
    border: 'border-emerald-500/20',
    glow: 'shadow-emerald-500/5'
  },
  {
    key: 'failed',
    label: 'Failed',
    icon: XCircleIcon,
    text: 'text-red-500 dark:text-red-400',
    bg: 'bg-red-500/10',
    border: 'border-red-500/20',
    glow: 'shadow-red-500/5'
  },
  {
    key: 'rate',
    label: 'Pass Rate',
    icon: ChartBarIcon,
    text: 'text-indigo-500 dark:text-indigo-400',
    bg: 'bg-indigo-500/10',
    border: 'border-indigo-500/20',
    glow: 'shadow-indigo-500/5'
  }
]

export default function MetricCards({ metrics }) {
  const passRate = metrics.total > 0
    ? `${Math.round((metrics.passed / metrics.total) * 100)}%`
    : '\u2014'

  const values = {
    total:  metrics.total  || '\u2014',
    passed: metrics.passed || '\u2014',
    failed: metrics.failed || '\u2014',
    rate:   passRate
  }

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {CARDS.map(({ key, label, icon: Icon, text, bg, border, glow }) => (
        <div
          key={key}
          className={`bg-white dark:bg-gray-900 border ${border} rounded-xl p-4 shadow-lg ${glow} hover:shadow-xl hover:border-opacity-40 transition-all`}
        >
          <div className="flex items-start justify-between mb-3">
            <span className="text-xs font-medium text-gray-500 uppercase tracking-wider">{label}</span>
            <div className={`${bg} p-1.5 rounded-lg`}>
              <Icon className={`w-4 h-4 ${text}`} />
            </div>
          </div>
          <p className={`text-3xl font-semibold tracking-tight ${text}`}>
            {values[key]}
          </p>
          {key === 'rate' && metrics.total > 0 && (
            <div className="mt-2 h-1 bg-gray-200 dark:bg-gray-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-indigo-500 rounded-full transition-all duration-500"
                style={{ width: passRate }}
              />
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
