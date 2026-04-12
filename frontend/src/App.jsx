import { useState, useRef, useEffect } from 'react'
import Sidebar from './components/layout/Sidebar'
import Topbar from './components/layout/Topbar'
import GoalInput from './components/GoalInput'
import MetricCards from './components/MetricCards'
import GraphView from './components/GraphView'
import EventStream from './components/EventStream'
import ScenarioTable from './components/ScenarioTable'
import { useSSE, SSE_EVENT_META } from './hooks/useSSE'

// Map SSE event type to GraphView node status
function nodeStatusForType(type) {
  if (type === 'result')    return 'done'
  if (type === 'report')    return 'done'
  if (type === 'error')     return 'error'
  if (type === 'diagnosis') return 'warn'
  return 'active'
}

// Format ISO timestamp to HH:MM:SS
function fmtTs(iso) {
  if (!iso) return timestamp()
  const d = new Date(iso)
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((n) => String(n).padStart(2, '0'))
    .join(':')
}

const INIT_SCENARIOS = [
  { name: 'OVP_4V2_100ms', type: 'overvoltage',  status: 'pending', attempts: 0, duration: '-' },
  { name: 'UVP_2V8_200ms', type: 'undervoltage', status: 'pending', attempts: 0, duration: '-' },
  { name: 'OCP_150A_50ms', type: 'overcurrent',  status: 'pending', attempts: 0, duration: '-' },
]

function timestamp() {
  const d = new Date()
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((n) => String(n).padStart(2, '0'))
    .join(':')
}

export default function App() {
  const [darkMode, setDarkMode]         = useState(true)
  const [demoRunning, setDemoRunning]   = useState(false)
  const [events, setEvents]             = useState([])
  const [scenarios, setScenarios]       = useState([])
  const [nodeStatuses, setNodeStatuses] = useState({})
  const timers = useRef([])

  useEffect(() => {
    document.documentElement.classList.toggle('dark', darkMode)
  }, [darkMode])

  function clearTimers() {
    timers.current.forEach(clearTimeout)
    timers.current = []
  }

  function pushEvent(node, message, level = 'info') {
    setEvents((prev) => [...prev, { node, message, level, time: timestamp() }])
  }

  function sched(ms, fn) {
    timers.current.push(setTimeout(fn, ms))
  }

  // ----- Real SSE path (used when backend is available) --------------------
  const sse = useSSE({
    onEvent(type, ev) {
      const meta  = SSE_EVENT_META[type] ?? { level: 'info', label: type }
      const label = `[${meta.label}]`
      setNodeStatuses((prev) => ({ ...prev, [ev.node]: nodeStatusForType(type) }))
      setEvents((prev) => [
        ...prev,
        { node: ev.node, message: `${label} ${ev.message}`, level: meta.level, time: fmtTs(ev.timestamp) }
      ])
      // Update scenario table from 'result' events
      if (type === 'result' && ev.data?.scenario_name) {
        setScenarios((prev) =>
          prev.map((s) =>
            s.name === ev.data.scenario_name
              ? { ...s, status: ev.data.status, attempts: ev.data.attempts ?? s.attempts, duration: ev.data.duration ?? s.duration }
              : s
          )
        )
      }
    },
    onDone() {
      setNodeStatuses((prev) => ({ ...prev, generate_report: 'done' }))
    },
    onError(ev) {
      setNodeStatuses((prev) => {
        const next = { ...prev }
        Object.keys(next).forEach((k) => { if (next[k] === 'active') next[k] = 'error' })
        return next
      })
      pushEvent(ev.node ?? 'system', ev.message, 'error')
    },
  })

  // ----- Demo simulation path (used when backend is NOT available) ---------
  function handleRun(goal) {
    // If backend proxy is reachable, use real SSE; otherwise fall back to demo.
    // To force demo mode set VITE_DEMO_MODE=true in .env
    if (import.meta.env.VITE_DEMO_MODE !== 'true') {
      setEvents([])
      setNodeStatuses({})
      setScenarios(INIT_SCENARIOS.map((s) => ({ ...s })))
      sse.start(goal)
      return
    }

    clearTimers()
    setDemoRunning(true)
    setEvents([])
    setNodeStatuses({})
    setScenarios(INIT_SCENARIOS.map((s) => ({ ...s })))

    // --- Demo simulation of LangGraph execution ---
    sched(0, () => {
      setNodeStatuses({ load_model: 'active' })
      pushEvent('load_model', 'Loading HIL model bms_ovp_test.hil...')
    })
    sched(900, () => {
      setNodeStatuses({ load_model: 'done' })
      pushEvent('load_model', 'Model loaded — 12 signals discovered', 'success')
    })
    sched(1400, () => {
      setNodeStatuses((p) => ({ ...p, plan_tests: 'active' }))
      pushEvent('plan_tests', `Sending goal to Claude Planner: "${goal}"`)
    })
    sched(2700, () => {
      setNodeStatuses((p) => ({ ...p, plan_tests: 'done' }))
      pushEvent('plan_tests', '3 test scenarios generated', 'success')
    })
    sched(3100, () => {
      setNodeStatuses((p) => ({ ...p, execute_scenario: 'active' }))
      pushEvent('execute_scenario', 'Scenario 1/3 [OVP_4V2_100ms] — applying voltage ramp')
    })
    sched(4700, () => {
      setNodeStatuses((p) => ({ ...p, execute_scenario: 'warn', analyze_failure: 'active' }))
      pushEvent('execute_scenario', 'FAIL: protection triggered at 105ms (limit: 100ms)', 'error')
      setScenarios((prev) => prev.map((s, i) => i === 0 ? { ...s, status: 'fail', attempts: 1 } : s))
      pushEvent('analyze_failure', 'Root cause: OVP_DELAY_CAL offset (+5ms bias)', 'warn')
    })
    sched(6000, () => {
      setNodeStatuses((p) => ({ ...p, analyze_failure: 'done', apply_fix: 'active' }))
      pushEvent('apply_fix', 'XCP write: OVP_DELAY_CAL 0x0069 -> 0x005F')
    })
    sched(7000, () => {
      setNodeStatuses((p) => ({ ...p, apply_fix: 'done', execute_scenario: 'active' }))
      pushEvent('apply_fix', 'Calibration applied via XCP DAQ', 'success')
      pushEvent('execute_scenario', 'Scenario 1/3 [OVP_4V2_100ms] retry #1 — re-applying ramp')
    })
    sched(8500, () => {
      setNodeStatuses((p) => ({ ...p, execute_scenario: 'done', advance_scenario: 'active' }))
      pushEvent('execute_scenario', 'PASS: protection at 99ms', 'success')
      setScenarios((prev) => prev.map((s, i) => i === 0 ? { ...s, status: 'pass', attempts: 2, duration: '8.5s' } : s))
    })
    sched(9300, () => {
      setNodeStatuses((p) => ({ ...p, advance_scenario: 'done', execute_scenario: 'active' }))
      pushEvent('execute_scenario', 'Scenario 2/3 [UVP_2V8_200ms] — applying discharge ramp')
    })
    sched(11000, () => {
      setNodeStatuses((p) => ({ ...p, execute_scenario: 'done', advance_scenario: 'active' }))
      pushEvent('execute_scenario', 'PASS: protection at 197ms', 'success')
      setScenarios((prev) => prev.map((s, i) => i === 1 ? { ...s, status: 'pass', attempts: 1, duration: '1.7s' } : s))
    })
    sched(11800, () => {
      setNodeStatuses((p) => ({ ...p, advance_scenario: 'done', execute_scenario: 'active' }))
      pushEvent('execute_scenario', 'Scenario 3/3 [OCP_150A_50ms] — applying current step')
    })
    sched(13300, () => {
      setNodeStatuses((p) => ({ ...p, execute_scenario: 'done', advance_scenario: 'active' }))
      pushEvent('execute_scenario', 'PASS: protection at 48ms', 'success')
      setScenarios((prev) => prev.map((s, i) => i === 2 ? { ...s, status: 'pass', attempts: 1, duration: '1.5s' } : s))
    })
    sched(14200, () => {
      setNodeStatuses((p) => ({ ...p, advance_scenario: 'done', generate_report: 'active' }))
      pushEvent('generate_report', 'Stopping simulation and rendering HTML report...')
    })
    sched(15400, () => {
      setNodeStatuses((p) => ({ ...p, generate_report: 'done' }))
      pushEvent('generate_report', 'Report saved: report_2026_04_12.html (3/3 passed)', 'success')
      setDemoRunning(false)
    })
  }

  const isRunning = sse.isRunning || demoRunning

  function handleStop() {
    sse.stop()
    clearTimers()
    setDemoRunning(false)
    setNodeStatuses((prev) => {
      const next = { ...prev }
      Object.keys(next).forEach((k) => { if (next[k] === 'active') next[k] = 'idle' })
      return next
    })
    pushEvent('system', 'Run stopped by user', 'warn')
  }

  const metrics = {
    total:  scenarios.length,
    passed: scenarios.filter((s) => s.status === 'pass').length,
    failed: scenarios.filter((s) => s.status === 'fail').length,
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 font-sans">
      <Sidebar />
      <Topbar darkMode={darkMode} onToggleDark={() => setDarkMode((d) => !d)} />

      <main className="ml-60 pt-16">
        <div className="px-6 py-6 max-w-7xl mx-auto space-y-6">

          {/* Page header */}
          <div className="flex items-start justify-between">
            <div>
              <h1 className="text-xl font-semibold text-white tracking-tight">
                HIL Verification Dashboard
              </h1>
              <p className="text-sm text-gray-500 mt-0.5">
                Typhoon HIL AI Agent — Automated Controller Verification
              </p>
            </div>
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-gray-800 border border-gray-700">
              <span
                className={`w-2 h-2 rounded-full ${
                  isRunning ? 'bg-emerald-500 animate-pulse' : 'bg-gray-600'
                }`}
              />
              <span className="text-xs font-medium text-gray-400">
                {isRunning ? 'Running' : 'Idle'}
              </span>
            </div>
          </div>

          <GoalInput onRun={handleRun} onStop={handleStop} isRunning={isRunning} />
          <MetricCards metrics={metrics} />

          {/* Graph + Event stream side by side */}
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
            <GraphView nodeStatuses={nodeStatuses} />
            <EventStream events={events} />
          </div>

          <ScenarioTable scenarios={scenarios} />
        </div>
      </main>
    </div>
  )
}
