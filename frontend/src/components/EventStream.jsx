import { useRef, useEffect } from 'react'
import {
  CheckCircleIcon,
  XCircleIcon,
  InformationCircleIcon,
  ExclamationTriangleIcon
} from '@heroicons/react/20/solid'

const LEVEL = {
  info:    { icon: InformationCircleIcon, text: 'text-sky-400',     dot: 'bg-sky-500' },
  success: { icon: CheckCircleIcon,       text: 'text-emerald-400', dot: 'bg-emerald-500' },
  error:   { icon: XCircleIcon,           text: 'text-red-400',     dot: 'bg-red-500' },
  warn:    { icon: ExclamationTriangleIcon, text: 'text-amber-400', dot: 'bg-amber-500' }
}

export default function EventStream({ events }) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events.length])

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl shadow-xl flex flex-col overflow-hidden" style={{ minHeight: '320px' }}>
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800 flex-shrink-0">
        <h2 className="text-sm font-semibold text-gray-200 tracking-tight">Event Stream</h2>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-600">{events.length} events</span>
          {events.length > 0 && (
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5 font-mono text-xs min-h-0" style={{ maxHeight: '280px' }}>
        {events.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40 gap-2">
            <div className="w-8 h-8 rounded-full border border-gray-700 flex items-center justify-center">
              <InformationCircleIcon className="w-4 h-4 text-gray-600" />
            </div>
            <p className="text-gray-600">Waiting for events...</p>
          </div>
        ) : (
          events.map((ev, i) => {
            const cfg = LEVEL[ev.level] ?? LEVEL.info
            const Icon = cfg.icon
            return (
              <div
                key={i}
                className="flex items-start gap-2 px-2 py-1.5 rounded-md hover:bg-gray-800/50 transition-colors group"
              >
                <Icon className={`w-3.5 h-3.5 mt-0.5 flex-shrink-0 ${cfg.text}`} />
                <div className="flex-1 min-w-0 flex items-baseline gap-2">
                  <span className={`font-semibold flex-shrink-0 ${cfg.text}`}>[{ev.node}]</span>
                  <span className="text-gray-400 truncate">{ev.message}</span>
                </div>
                <span className="text-gray-600 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                  {ev.time}
                </span>
              </div>
            )
          })
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
