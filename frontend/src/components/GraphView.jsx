// LangGraph node topology visualizer with animated edges and healing loop emphasis

const W = 120
const H = 36

const NODES = [
  { id: 'load_model',       label: 'Load Model',   x: 170, y: 20  },
  { id: 'plan_tests',       label: 'Plan Tests',   x: 170, y: 100 },
  { id: 'execute_scenario', label: 'Execute',      x: 170, y: 190 },
  { id: 'analyze_failure',  label: 'Analyze Fail', x: 40,  y: 300 },
  { id: 'apply_fix',        label: 'Apply Fix',    x: 40,  y: 400 },
  { id: 'advance_scenario', label: 'Advance',      x: 300, y: 300 },
  { id: 'generate_report',  label: 'Report',       x: 300, y: 400 },
]

const cx = (n) => n.x + W / 2
const cy = (n) => n.y + H / 2
const bot = (n) => n.y + H
const top = (n) => n.y

const COLORS = {
  idle:   { fill: '#f8fafc', fillDark: '#111827', stroke: '#d1d5db', strokeDark: '#374151', text: '#6b7280', textDark: '#6B7280' },
  active: { fill: '#eff6ff', fillDark: '#1E3A5F', stroke: '#3b82f6', strokeDark: '#3B82F6', text: '#2563eb', textDark: '#93C5FD' },
  done:   { fill: '#f0fdf4', fillDark: '#052E16', stroke: '#22c55e', strokeDark: '#22C55E', text: '#16a34a', textDark: '#86EFAC' },
  warn:   { fill: '#fffbeb', fillDark: '#451A03', stroke: '#f59e0b', strokeDark: '#F59E0B', text: '#d97706', textDark: '#FCD34D' },
  error:  { fill: '#fef2f2', fillDark: '#450A0A', stroke: '#ef4444', strokeDark: '#EF4444', text: '#dc2626', textDark: '#FCA5A5' },
}

// Healing loop edges (amber pulse when active)
const HEAL_EDGES = new Set(['e3', 'e5', 'e7'])

const EDGES = [
  { id: 'e1', path: `M 230 ${bot(NODES[0])} L 230 ${top(NODES[1])}`, label: null },
  { id: 'e2', path: `M 230 ${bot(NODES[1])} L 230 ${top(NODES[2])}`, label: null },
  { id: 'e3', path: `M 215 ${bot(NODES[2])} L 105 ${top(NODES[3])}`, label: 'fail',  lx: 148, ly: 258 },
  { id: 'e4', path: `M 245 ${bot(NODES[2])} L 355 ${top(NODES[5])}`, label: 'pass',  lx: 308, ly: 258 },
  { id: 'e5', path: `M 100 ${bot(NODES[3])} L 100 ${top(NODES[4])}`, label: null },
  { id: 'e6', path: `M 360 ${bot(NODES[5])} L 360 ${top(NODES[6])}`, label: null },
  { id: 'e7', path: `M 40 ${cy(NODES[4])} C 5 ${cy(NODES[4])} 5 ${cy(NODES[2])} 170 ${cy(NODES[2])}`, label: 'retry', lx: 14, ly: 310, dashed: true },
  { id: 'e8', path: `M 420 ${cy(NODES[5])} C 450 ${cy(NODES[5])} 450 ${cy(NODES[2])} 290 ${cy(NODES[2])}`, label: 'more', lx: 445, ly: 262, dashed: true },
]

export default function GraphView({ nodeStatuses }) {
  const isDark = document.documentElement.classList.contains('dark')

  function getColors(id) {
    const status = nodeStatuses[id] ?? 'idle'
    const c = COLORS[status] ?? COLORS.idle
    return {
      fill: isDark ? c.fillDark : c.fill,
      stroke: isDark ? c.strokeDark : c.stroke,
      text: isDark ? c.textDark : c.text,
    }
  }

  // Detect active healing loop
  const healingActive = ['analyze_failure', 'apply_fix'].some(
    (n) => nodeStatuses[n] === 'active' || nodeStatuses[n] === 'warn'
  )

  function getEdgeColor(edgeId) {
    if (healingActive && HEAL_EDGES.has(edgeId)) return isDark ? '#F59E0B' : '#d97706'
    return isDark ? '#374151' : '#d1d5db'
  }

  const legendItems = [
    { label: 'Idle',   color: isDark ? COLORS.idle.strokeDark   : COLORS.idle.stroke },
    { label: 'Active', color: isDark ? COLORS.active.strokeDark : COLORS.active.stroke },
    { label: 'Done',   color: isDark ? COLORS.done.strokeDark   : COLORS.done.stroke },
    { label: 'Retry',  color: isDark ? COLORS.warn.strokeDark   : COLORS.warn.stroke },
    { label: 'Error',  color: isDark ? COLORS.error.strokeDark  : COLORS.error.stroke },
  ]

  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl shadow-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800">
        <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-200 tracking-tight">Graph State</h2>
        <div className="flex items-center gap-3">
          {legendItems.map(({ label, color }) => (
            <span key={label} className="flex items-center gap-1 text-xs text-gray-500">
              <span className="w-2 h-2 rounded-full" style={{ background: color }} />
              {label}
            </span>
          ))}
        </div>
      </div>

      <div className="p-4">
        <svg viewBox="0 0 460 456" className="w-full" style={{ height: '280px' }}>
          <defs>
            <marker id="arr" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
              <path d="M0,0 L0,6 L8,3 z" fill={isDark ? '#4B5563' : '#9ca3af'} />
            </marker>
            <marker id="arr-heal" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
              <path d="M0,0 L0,6 L8,3 z" fill="#F59E0B" />
            </marker>
          </defs>

          {/* Edges */}
          {EDGES.map((e) => {
            const color = getEdgeColor(e.id)
            const isHealEdge = healingActive && HEAL_EDGES.has(e.id)
            return (
              <g key={e.id}>
                <path
                  d={e.path}
                  fill="none"
                  stroke={color}
                  strokeWidth={isHealEdge ? 2 : 1.5}
                  strokeDasharray={e.dashed ? '5 3' : undefined}
                  markerEnd={isHealEdge ? 'url(#arr-heal)' : 'url(#arr)'}
                >
                  {isHealEdge && (
                    <animate
                      attributeName="stroke-dashoffset"
                      values="16;0"
                      dur="0.8s"
                      repeatCount="indefinite"
                    />
                  )}
                </path>
                {e.label && (
                  <text
                    x={e.lx} y={e.ly}
                    fill={isHealEdge ? '#F59E0B' : (isDark ? '#6B7280' : '#9ca3af')}
                    fontSize="9"
                    fontFamily="monospace"
                    textAnchor="middle"
                    fontWeight={isHealEdge ? 'bold' : 'normal'}
                  >
                    {e.label}
                  </text>
                )}
              </g>
            )
          })}

          {/* Nodes */}
          {NODES.map((node) => {
            const c = getColors(node.id)
            const isActive = (nodeStatuses[node.id] ?? 'idle') === 'active'
            return (
              <g key={node.id}>
                {isActive && (
                  <rect
                    x={node.x - 3} y={node.y - 3}
                    width={W + 6} height={H + 6}
                    rx="11" fill="none"
                    stroke={c.stroke} strokeWidth="1"
                    opacity="0.35"
                  >
                    <animate
                      attributeName="opacity"
                      values="0.35;0.7;0.35"
                      dur="1.4s"
                      repeatCount="indefinite"
                    />
                  </rect>
                )}
                <rect
                  x={node.x} y={node.y}
                  width={W} height={H}
                  rx="8"
                  fill={c.fill}
                  stroke={c.stroke}
                  strokeWidth="1.5"
                />
                <text
                  x={cx(node)} y={cy(node) + 1}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fill={c.text}
                  fontSize="11"
                  fontWeight="500"
                  fontFamily="Inter, system-ui, sans-serif"
                >
                  {node.label}
                </text>
              </g>
            )
          })}
        </svg>
      </div>
    </div>
  )
}
