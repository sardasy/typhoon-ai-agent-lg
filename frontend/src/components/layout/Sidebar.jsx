import {
  HomeIcon,
  ChartBarIcon,
  DocumentChartBarIcon,
  CodeBracketIcon,
  BoltIcon,
  BeakerIcon
} from '@heroicons/react/24/outline'

const NAV = [
  { id: 'dashboard',  label: 'Dashboard',      icon: HomeIcon },
  { id: 'analytics',  label: 'Analytics',       icon: ChartBarIcon },
  { id: 'codegen',    label: 'Code Generator',  icon: CodeBracketIcon },
  { id: 'reports',    label: 'Reports',         icon: DocumentChartBarIcon },
]

export default function Sidebar({ activeView, onNavigate }) {
  return (
    <aside className="fixed inset-y-0 left-0 w-60 bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-800 flex flex-col z-20">
      {/* Logo */}
      <div className="h-16 flex items-center px-5 border-b border-gray-200 dark:border-gray-800 flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-indigo-600 rounded-lg flex items-center justify-center shadow-lg shadow-indigo-600/30">
            <BoltIcon className="w-5 h-5 text-white" />
          </div>
          <div>
            <p className="text-sm font-semibold text-gray-900 dark:text-white tracking-tight leading-none">THAA</p>
            <p className="text-xs text-gray-500 mt-0.5">HIL AI Agent</p>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        <p className="px-3 mb-2 text-xs font-medium text-gray-400 dark:text-gray-600 uppercase tracking-wider">Menu</p>
        {NAV.map(({ id, label, icon: Icon }) => {
          const active = activeView === id
          return (
            <button
              key={id}
              onClick={() => onNavigate(id)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                active
                  ? 'bg-indigo-600/15 text-indigo-600 dark:text-indigo-400 shadow-sm'
                  : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-700 dark:hover:text-gray-200'
              }`}
            >
              <Icon className="w-5 h-5 flex-shrink-0" />
              {label}
              {active && (
                <span className="ml-auto w-1.5 h-1.5 rounded-full bg-indigo-500" />
              )}
            </button>
          )
        })}
      </nav>

      {/* Phase badge */}
      <div className="px-4 py-4 border-t border-gray-200 dark:border-gray-800">
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-800/50">
          <BeakerIcon className="w-4 h-4 text-indigo-400 flex-shrink-0" />
          <div>
            <p className="text-xs font-medium text-gray-700 dark:text-gray-300">Phase 2</p>
            <p className="text-xs text-gray-500">Self-Healing Loop</p>
          </div>
        </div>
      </div>
    </aside>
  )
}
