import { useState, useRef, useEffect } from 'react'
import Sidebar from './components/layout/Sidebar'
import Topbar from './components/layout/Topbar'
import GoalInput from './components/GoalInput'
import MetricCards from './components/MetricCards'
import GraphView from './components/GraphView'
import EventStream from './components/EventStream'
import ScenarioTable from './components/ScenarioTable'
import HealingLoopPanel from './components/HealingLoopPanel'
import WaveformCharts from './components/WaveformCharts'
import ReportViewer from './components/ReportViewer'
import CodeGenerator from './components/CodeGenerator'
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

function timestamp() {
  const d = new Date()
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((n) => String(n).padStart(2, '0'))
    .join(':')
}

export default function App() {
  // --- UI state ---
  const [darkMode, setDarkMode]         = useState(() => {
    const saved = localStorage.getItem('thaa-dark-mode')
    return saved !== null ? saved === 'true' : true
  })
  const [activeView, setActiveView]     = useState('dashboard')
  const [searchQuery, setSearchQuery]   = useState('')

  // --- Run state ---
  const [demoRunning, setDemoRunning]   = useState(false)
  const [events, setEvents]             = useState([])
  const [scenarios, setScenarios]       = useState([])
  const [nodeStatuses, setNodeStatuses] = useState({})

  // --- Expanded data state ---
  const [scenarioResults, setScenarioResults]       = useState([])
  const [healingHistory, setHealingHistory]          = useState([])
  const [selectedScenarioId, setSelectedScenarioId]  = useState(null)

  const timers = useRef([])

  useEffect(() => {
    document.documentElement.classList.toggle('dark', darkMode)
    localStorage.setItem('thaa-dark-mode', String(darkMode))
  }, [darkMode])

  function clearTimers() {
    timers.current.forEach(clearTimeout)
    timers.current = []
  }

  function pushEvent(node, message, level = 'info', data = null) {
    setEvents((prev) => [...prev, { node, message, level, time: timestamp(), data }])
  }

  function sched(ms, fn) {
    timers.current.push(setTimeout(fn, ms))
  }

  // ----- Real SSE path -------------------------------------------------------
  const sse = useSSE({
    onEvent(type, ev) {
      const meta  = SSE_EVENT_META[type] ?? { level: 'info', label: type }
      const label = `[${meta.label}]`
      setNodeStatuses((prev) => ({ ...prev, [ev.node]: nodeStatusForType(type) }))
      setEvents((prev) => [
        ...prev,
        { node: ev.node, message: `${label} ${ev.message}`, level: meta.level, time: fmtTs(ev.timestamp), data: ev.data }
      ])

      // Update scenario table from 'result' events
      if (type === 'result' && ev.data) {
        const d = ev.data
        setScenarioResults((prev) => [...prev, d])
        if (d.scenario_name || d.scenario_id) {
          const key = d.scenario_name || d.scenario_id
          setScenarios((prev) => {
            const exists = prev.some((s) => s.name === key)
            if (exists) {
              return prev.map((s) =>
                s.name === key
                  ? { ...s, status: d.status, attempts: d.retry_count ?? d.attempts ?? s.attempts, duration: d.duration_s ? `${d.duration_s.toFixed(1)}s` : s.duration }
                  : s
              )
            }
            return [...prev, { name: key, type: d.category || 'test', status: d.status, attempts: d.retry_count ?? 1, duration: d.duration_s ? `${d.duration_s.toFixed(1)}s` : '-' }]
          })
        }
      }

      // Accumulate healing history from diagnosis events
      if (type === 'diagnosis' && ev.data) {
        setHealingHistory((prev) => [...prev, { diagnosis: ev.data, fix: null, outcome: null }])
      }

      // Update last healing entry with fix details
      if (type === 'action' && ev.node === 'apply_fix' && ev.data) {
        setHealingHistory((prev) => {
          if (prev.length === 0) return prev
          const updated = [...prev]
          updated[updated.length - 1] = { ...updated[updated.length - 1], fix: ev.data }
          return updated
        })
      }

      // Track plan events - update scenario list from plan data
      if (type === 'plan' && ev.data?.scenario_count) {
        const count = ev.data.scenario_count
        if (scenarios.length === 0 && count > 0) {
          const names = ev.data.scenario_names || []
          if (names.length > 0) {
            setScenarios(names.map((n) => ({ name: n, type: 'test', status: 'pending', attempts: 0, duration: '-' })))
          }
        }
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

  // ----- Demo simulation path ------------------------------------------------
  function handleRun(goal) {
    if (import.meta.env.VITE_DEMO_MODE !== 'true') {
      setEvents([])
      setNodeStatuses({})
      setScenarios([])
      setScenarioResults([])
      setHealingHistory([])
      setSelectedScenarioId(null)
      sse.start(goal)
      return
    }

    clearTimers()
    setDemoRunning(true)
    setEvents([])
    setNodeStatuses({})
    setScenarioResults([])
    setHealingHistory([])
    setSelectedScenarioId(null)

    const INIT_SCENARIOS = [
      { name: 'OVP_4V2_100ms', type: 'overvoltage',  status: 'pending', attempts: 0, duration: '-' },
      { name: 'UVP_2V8_200ms', type: 'undervoltage', status: 'pending', attempts: 0, duration: '-' },
      { name: 'OCP_150A_50ms', type: 'overcurrent',  status: 'pending', attempts: 0, duration: '-' },
    ]
    setScenarios(INIT_SCENARIOS.map((s) => ({ ...s })))

    // --- Demo simulation of LangGraph execution ---
    sched(0, () => {
      setNodeStatuses({ load_model: 'active' })
      pushEvent('load_model', 'Loading HIL model bms_ovp_test.hil...')
    })
    sched(900, () => {
      setNodeStatuses({ load_model: 'done' })
      pushEvent('load_model', 'Model loaded - 12 signals discovered', 'success')
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
      pushEvent('execute_scenario', 'Scenario 1/3 [OVP_4V2_100ms] - applying voltage ramp')
    })
    sched(4700, () => {
      setNodeStatuses((p) => ({ ...p, execute_scenario: 'warn', analyze_failure: 'active' }))
      const failResult = {
        scenario_id: 'OVP_4V2_100ms', status: 'fail', duration_s: 1.6,
        waveform_stats: [
          { signal: 'V_cell_1', mean: 4.18, max: 4.22, min: 3.6, rms: 4.15, overshoot_percent: 2.3, rise_time_ms: 45, settling_time_ms: 105 },
          { signal: 'BMS_OVP_relay', mean: 0.8, max: 1.0, min: 0.0, rms: 0.7, overshoot_percent: 0, rise_time_ms: 105, settling_time_ms: 110 }
        ],
        fail_reason: 'Protection triggered at 105ms (limit: 100ms)', retry_count: 1
      }
      pushEvent('execute_scenario', 'FAIL: protection triggered at 105ms (limit: 100ms)', 'error', failResult)
      setScenarioResults((prev) => [...prev, failResult])
      setScenarios((prev) => prev.map((s, i) => i === 0 ? { ...s, status: 'fail', attempts: 1 } : s))

      const diagData = {
        failed_scenario_id: 'OVP_4V2_100ms',
        root_cause_category: 'tuning',
        root_cause_description: 'OVP_DELAY_CAL offset (+5ms bias in scan interval)',
        confidence: 0.87,
        corrective_action_type: 'xcp_calibration',
        corrective_param: 'OVP_DELAY_CAL',
        corrective_value: 95,
        evidence: ['Relay response measured at 105ms', 'Historical: similar +5ms offset resolved with CAL adjust', 'No electrical anomaly in waveform']
      }
      pushEvent('analyze_failure', 'Root cause: OVP_DELAY_CAL offset (+5ms bias)', 'warn', diagData)
      setHealingHistory((prev) => [...prev, { diagnosis: diagData, fix: null, outcome: null }])
    })
    sched(6000, () => {
      setNodeStatuses((p) => ({ ...p, analyze_failure: 'done', apply_fix: 'active' }))
      const fixData = { param: 'OVP_DELAY_CAL', old_value: 100, new_value: 95, status: 'success' }
      pushEvent('apply_fix', 'XCP write: OVP_DELAY_CAL 0x0069 -> 0x005F', 'info', fixData)
      setHealingHistory((prev) => {
        if (prev.length === 0) return prev
        const updated = [...prev]
        updated[updated.length - 1] = { ...updated[updated.length - 1], fix: fixData }
        return updated
      })
    })
    sched(7000, () => {
      setNodeStatuses((p) => ({ ...p, apply_fix: 'done', execute_scenario: 'active' }))
      pushEvent('apply_fix', 'Calibration applied via XCP DAQ', 'success')
      pushEvent('execute_scenario', 'Scenario 1/3 [OVP_4V2_100ms] retry #1 - re-applying ramp')
    })
    sched(8500, () => {
      setNodeStatuses((p) => ({ ...p, execute_scenario: 'done', advance_scenario: 'active' }))
      const passResult = {
        scenario_id: 'OVP_4V2_100ms', status: 'pass', duration_s: 8.5,
        waveform_stats: [
          { signal: 'V_cell_1', mean: 4.19, max: 4.21, min: 3.6, rms: 4.16, overshoot_percent: 1.1, rise_time_ms: 44, settling_time_ms: 97 },
          { signal: 'BMS_OVP_relay', mean: 0.9, max: 1.0, min: 0.0, rms: 0.8, overshoot_percent: 0, rise_time_ms: 97, settling_time_ms: 99 }
        ],
        fail_reason: '', retry_count: 2
      }
      pushEvent('execute_scenario', 'PASS: protection at 99ms', 'success', passResult)
      setScenarioResults((prev) => [...prev, passResult])
      setScenarios((prev) => prev.map((s, i) => i === 0 ? { ...s, status: 'pass', attempts: 2, duration: '8.5s' } : s))
      setHealingHistory((prev) => {
        if (prev.length === 0) return prev
        const updated = [...prev]
        updated[updated.length - 1] = { ...updated[updated.length - 1], outcome: 'pass' }
        return updated
      })
    })
    sched(9300, () => {
      setNodeStatuses((p) => ({ ...p, advance_scenario: 'done', execute_scenario: 'active' }))
      pushEvent('execute_scenario', 'Scenario 2/3 [UVP_2V8_200ms] - applying discharge ramp')
    })
    sched(11000, () => {
      setNodeStatuses((p) => ({ ...p, execute_scenario: 'done', advance_scenario: 'active' }))
      const passResult2 = {
        scenario_id: 'UVP_2V8_200ms', status: 'pass', duration_s: 1.7,
        waveform_stats: [
          { signal: 'V_cell_1', mean: 2.85, max: 3.6, min: 2.78, rms: 2.9, overshoot_percent: 0.5, rise_time_ms: 120, settling_time_ms: 190 }
        ],
        fail_reason: '', retry_count: 0
      }
      pushEvent('execute_scenario', 'PASS: protection at 197ms', 'success', passResult2)
      setScenarioResults((prev) => [...prev, passResult2])
      setScenarios((prev) => prev.map((s, i) => i === 1 ? { ...s, status: 'pass', attempts: 1, duration: '1.7s' } : s))
    })
    sched(11800, () => {
      setNodeStatuses((p) => ({ ...p, advance_scenario: 'done', execute_scenario: 'active' }))
      pushEvent('execute_scenario', 'Scenario 3/3 [OCP_150A_50ms] - applying current step')
    })
    sched(13300, () => {
      setNodeStatuses((p) => ({ ...p, execute_scenario: 'done', advance_scenario: 'active' }))
      const passResult3 = {
        scenario_id: 'OCP_150A_50ms', status: 'pass', duration_s: 1.5,
        waveform_stats: [
          { signal: 'I_pack', mean: 145, max: 152, min: 0, rms: 140, overshoot_percent: 1.3, rise_time_ms: 22, settling_time_ms: 45 }
        ],
        fail_reason: '', retry_count: 0
      }
      pushEvent('execute_scenario', 'PASS: protection at 48ms', 'success', passResult3)
      setScenarioResults((prev) => [...prev, passResult3])
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

  // ----- View rendering ------------------------------------------------------
  function renderDashboard() {
    return (
      <>
        <GoalInput onRun={handleRun} onStop={handleStop} isRunning={isRunning} />
        <MetricCards metrics={metrics} />

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          <GraphView nodeStatuses={nodeStatuses} />
          <EventStream events={events} searchQuery={searchQuery} />
        </div>

        <ScenarioTable
          scenarios={scenarios}
          selectedId={selectedScenarioId}
          onSelect={setSelectedScenarioId}
        />
      </>
    )
  }

  function renderAnalytics() {
    return (
      <>
        <MetricCards metrics={metrics} />
        <HealingLoopPanel healingHistory={healingHistory} />
        <WaveformCharts
          scenarioResults={scenarioResults}
          selectedScenarioId={selectedScenarioId}
          onSelectScenario={setSelectedScenarioId}
        />
      </>
    )
  }

  function renderReports() {
    return <ReportViewer />
  }

  function renderCodegen() {
    return <CodeGenerator />
  }

  const VIEW_TITLES = {
    dashboard: { title: 'HIL Verification Dashboard', sub: 'Typhoon HIL AI Agent \u2014 Automated Controller Verification' },
    analytics: { title: 'Analytics & Diagnostics', sub: 'Self-healing loop visualization and waveform analysis' },
    codegen:   { title: 'HTAF Code Generator', sub: 'Generate pytest test code from .tse model files' },
    reports:   { title: 'Test Reports', sub: 'Browse and view generated verification reports' },
  }

  const viewInfo = VIEW_TITLES[activeView] || VIEW_TITLES.dashboard

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950 text-gray-900 dark:text-gray-100 font-sans transition-colors">
      <Sidebar activeView={activeView} onNavigate={setActiveView} />
      <Topbar
        darkMode={darkMode}
        onToggleDark={() => setDarkMode((d) => !d)}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
      />

      <main className="ml-60 pt-16">
        <div className="px-6 py-6 max-w-7xl mx-auto space-y-6">

          {/* Page header */}
          <div className="flex items-start justify-between">
            <div>
              <h1 className="text-xl font-semibold text-gray-900 dark:text-white tracking-tight">
                {viewInfo.title}
              </h1>
              <p className="text-sm text-gray-500 mt-0.5">
                {viewInfo.sub}
              </p>
            </div>
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-gray-200 dark:bg-gray-800 border border-gray-300 dark:border-gray-700">
              <span
                className={`w-2 h-2 rounded-full ${
                  isRunning ? 'bg-emerald-500 animate-pulse' : 'bg-gray-400 dark:bg-gray-600'
                }`}
              />
              <span className="text-xs font-medium text-gray-500 dark:text-gray-400">
                {isRunning ? 'Running' : 'Idle'}
              </span>
            </div>
          </div>

          {activeView === 'dashboard' && renderDashboard()}
          {activeView === 'analytics' && renderAnalytics()}
          {activeView === 'codegen' && renderCodegen()}
          {activeView === 'reports' && renderReports()}
        </div>
      </main>
    </div>
  )
}
