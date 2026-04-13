import { useRef, useEffect, useState, useMemo } from 'react'
import {
  CheckCircleIcon,
  XCircleIcon,
  InformationCircleIcon,
  ExclamationTriangleIcon,
  ChevronDownIcon,
  FunnelIcon
} from '@heroicons/react/20/solid'

const LEVEL = {
  info:    { icon: InformationCircleIcon, text: 'text-sky-500 dark:text-sky-400',     dot: 'bg-sky-500' },
  success: { icon: CheckCircleIcon,       text: 'text-emerald-500 dark:text-emerald-400', dot: 'bg-emerald-500' },
  error:   { icon: XCircleIcon,           text: 'text-red-500 dark:text-red-400',     dot: 'bg-red-500' },
  warn:    { icon: ExclamationTriangleIcon, text: 'text-amber-500 dark:text-amber-400', dot: 'bg-amber-500' }
}

const FILTER_TYPES = ['thought', 'plan', 'action', 'observation', 'result', 'diagnosis', 'report', 'error']

export default function EventStream({ events, searchQuery }) {
  const bottomRef = useRef(null)
  const [expandedIdx, setExpandedIdx] = useState(null)
  const [activeFilters, setActiveFilters] = useState(new Set(FILTER_TYPES))

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events.length])

  function toggleFilter(f) {
    setActiveFilters((prev) => {
      const next = new Set(prev)
      if (next.has(f)) next.delete(f)
      else next.add(f)
      return next
    })
  }

  const filteredEvents = useMemo(() => {
    return events.filter((ev) => {
      // Search filter
      if (searchQuery && !ev.message.toLowerCase().includes(searchQuery.toLowerCase()) && !ev.node.toLowerCase().includes(searchQuery.toLowerCase())) {
        return false
      }
      // Type filter - extract type from message tag like [Plan], [Result], etc.
      const tagMatch = ev.message.match(/^\[(\w+)\]/)
      if (tagMatch) {
        const tag = tagMatch[1].toLowerCase()
        if (!activeFilters.has(tag)) return false
      }
      return true
    })
  }, [events, searchQuery, activeFilters])

  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl shadow-xl flex flex-col overflow-hidden" style={{ minHeight: '320px' }}>
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800 flex-shrink-0">
        <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-200 tracking-tight">Event Stream</h2>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-400 dark:text-gray-600">{filteredEvents.length}/{events.length} events</span>
          {events.length > 0 && (
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
          )}
        </div>
      </div>

      {/* Filter chips */}
      <div className="flex items-center gap-1.5 px-4 py-2 border-b border-gray-100 dark:border-gray-800/50 overflow-x-auto flex-shrink-0">
        <FunnelIcon className="w-3 h-3 text-gray-400 flex-shrink-0" />
        {FILTER_TYPES.map((f) => (
          <button
            key={f}
            onClick={() => toggleFilter(f)}
            className={`px-2 py-0.5 rounded text-xs font-medium transition-colors flex-shrink-0 ${
              activeFilters.has(f)
                ? 'bg-indigo-100 dark:bg-indigo-500/20 text-indigo-600 dark:text-indigo-400'
                : 'bg-gray-100 dark:bg-gray-800 text-gray-400 dark:text-gray-600'
            }`}
          >
            {f}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5 font-mono text-xs min-h-0" style={{ maxHeight: '280px' }}>
        {filteredEvents.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40 gap-2">
            <div className="w-8 h-8 rounded-full border border-gray-300 dark:border-gray-700 flex items-center justify-center">
              <InformationCircleIcon className="w-4 h-4 text-gray-400 dark:text-gray-600" />
            </div>
            <p className="text-gray-400 dark:text-gray-600">Waiting for events...</p>
          </div>
        ) : (
          filteredEvents.map((ev, i) => {
            const cfg = LEVEL[ev.level] ?? LEVEL.info
            const Icon = cfg.icon
            const hasData = ev.data && Object.keys(ev.data).length > 0
            const isExpanded = expandedIdx === i
            return (
              <div key={i}>
                <div
                  className={`flex items-start gap-2 px-2 py-1.5 rounded-md hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors group ${hasData ? 'cursor-pointer' : ''}`}
                  onClick={() => hasData && setExpandedIdx(isExpanded ? null : i)}
                >
                  <Icon className={`w-3.5 h-3.5 mt-0.5 flex-shrink-0 ${cfg.text}`} />
                  <div className="flex-1 min-w-0 flex items-baseline gap-2">
                    <span className={`font-semibold flex-shrink-0 ${cfg.text}`}>[{ev.node}]</span>
                    <span className="text-gray-600 dark:text-gray-400 truncate">{ev.message}</span>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <span className="text-gray-400 dark:text-gray-600 opacity-0 group-hover:opacity-100 transition-opacity">
                      {ev.time}
                    </span>
                    {hasData && (
                      <ChevronDownIcon className={`w-3 h-3 text-gray-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
                    )}
                  </div>
                </div>
                {isExpanded && hasData && (
                  <div className="ml-7 mr-2 mb-1 px-3 py-2 rounded bg-gray-50 dark:bg-gray-800/70 border border-gray-200 dark:border-gray-700/50 text-xs text-gray-600 dark:text-gray-400 overflow-x-auto">
                    <pre className="whitespace-pre-wrap">{JSON.stringify(ev.data, null, 2)}</pre>
                  </div>
                )}
              </div>
            )
          })
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
