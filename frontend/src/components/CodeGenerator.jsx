import { useState, useRef, useCallback } from 'react'
import {
  ArrowUpTrayIcon,
  CodeBracketIcon,
  PlayIcon,
  ArrowDownTrayIcon,
  CheckCircleIcon,
  XCircleIcon,
  DocumentTextIcon,
  CpuChipIcon,
  BeakerIcon,
} from '@heroicons/react/24/outline'

const PIPELINE_STEPS = [
  { id: 'parse_tse',        label: 'Parse TSE' },
  { id: 'map_requirements', label: 'Map Requirements' },
  { id: 'generate_tests',   label: 'Generate Tests' },
  { id: 'validate_code',    label: 'Validate' },
  { id: 'export_tests',     label: 'Export' },
]

export default function CodeGenerator() {
  const [tseContent, setTseContent] = useState('')
  const [tsePath, setTsePath] = useState('')
  const [mode, setMode] = useState('mock')
  const [running, setRunning] = useState(false)
  const [events, setEvents] = useState([])
  const [stepStatuses, setStepStatuses] = useState({})
  const [generatedFiles, setGeneratedFiles] = useState({})
  const [activeFileTab, setActiveFileTab] = useState(null)
  const [downloadZip, setDownloadZip] = useState(null)
  const [dragOver, setDragOver] = useState(false)
  const fileRef = useRef(null)
  const abortRef = useRef(null)

  // File upload handler
  const handleFile = useCallback(async (file) => {
    if (!file || !file.name.endsWith('.tse')) {
      setEvents((p) => [...p, { level: 'error', msg: 'Only .tse files are accepted' }])
      return
    }
    const text = await file.text()
    setTseContent(text)
    setTsePath(file.name)
    setEvents((p) => [...p, { level: 'success', msg: `Loaded ${file.name} (${(file.size / 1024).toFixed(1)} KB)` }])
  }, [])

  const onDrop = useCallback((e) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer?.files?.[0]
    if (file) handleFile(file)
  }, [handleFile])

  const onFileInput = useCallback((e) => {
    const file = e.target?.files?.[0]
    if (file) handleFile(file)
  }, [handleFile])

  // Run codegen pipeline
  async function handleGenerate() {
    if (!tseContent || running) return
    setRunning(true)
    setEvents([])
    setStepStatuses({})
    setGeneratedFiles({})
    setActiveFileTab(null)
    setDownloadZip(null)

    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl

    try {
      const res = await fetch('/api/generate-tests', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tse_content: tseContent, tse_path: tsePath, mode }),
        signal: ctrl.signal,
      })

      if (!res.ok) {
        const body = await res.text()
        setEvents((p) => [...p, { level: 'error', msg: body || `HTTP ${res.status}` }])
        setRunning(false)
        return
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const blocks = buffer.split(/\n\n|\r\n\r\n/)
        buffer = blocks.pop()

        for (const block of blocks) {
          if (!block.trim()) continue
          let type = 'message'
          const dataLines = []
          for (const line of block.split(/\r?\n/)) {
            if (line.startsWith('event:')) type = line.slice(6).trim()
            else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
          }
          const raw = dataLines.join('\n')
          if (!raw) continue

          let payload
          try { payload = JSON.parse(raw) } catch { payload = { message: raw } }

          // Update step status
          const node = payload.node || ''
          if (PIPELINE_STEPS.some((s) => s.id === node)) {
            setStepStatuses((prev) => {
              const next = { ...prev }
              // Mark current as active, previous as done
              let found = false
              for (const s of PIPELINE_STEPS) {
                if (s.id === node) { next[s.id] = type === 'error' ? 'error' : 'active'; found = true }
                else if (!found) next[s.id] = 'done'
              }
              return next
            })
          }

          // Capture generated files
          if (type === 'files' && payload.data?.files) {
            const files = payload.data.files
            setGeneratedFiles(files)
            const firstKey = Object.keys(files)[0]
            if (firstKey) setActiveFileTab(firstKey)
          }

          // Capture download link
          if (type === 'report' && payload.data?.zip_file) {
            setDownloadZip(payload.data.zip_file)
            setStepStatuses((prev) => ({ ...prev, export_tests: 'done' }))
          }

          setEvents((p) => [...p, {
            level: type === 'error' ? 'error' : type === 'observation' ? 'info' : 'success',
            msg: payload.message || type,
            node,
          }])
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        setEvents((p) => [...p, { level: 'error', msg: err.message }])
      }
    } finally {
      setRunning(false)
    }
  }

  const fileKeys = Object.keys(generatedFiles)

  return (
    <div className="space-y-6">
      {/* Upload zone */}
      <div
        className={`bg-white dark:bg-gray-900 border-2 border-dashed rounded-xl p-8 text-center transition-colors ${
          dragOver
            ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-500/10'
            : 'border-gray-300 dark:border-gray-700'
        }`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        <ArrowUpTrayIcon className="w-10 h-10 mx-auto text-gray-400 dark:text-gray-600 mb-3" />
        <p className="text-sm text-gray-600 dark:text-gray-400 mb-2">
          {tseContent
            ? <span className="text-emerald-600 dark:text-emerald-400 font-medium">{tsePath} loaded</span>
            : 'Drag & drop a .tse model file here'}
        </p>
        <input ref={fileRef} type="file" accept=".tse" className="hidden" onChange={onFileInput} />
        <button
          onClick={() => fileRef.current?.click()}
          className="text-sm text-indigo-600 dark:text-indigo-400 hover:underline"
        >
          or click to browse
        </button>
      </div>

      {/* Mode selector + Generate button */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-3 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-lg px-4 py-2">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio" name="mode" value="mock"
              checked={mode === 'mock'} onChange={() => setMode('mock')}
              className="text-indigo-600"
            />
            <BeakerIcon className="w-4 h-4 text-gray-500" />
            <span className="text-sm text-gray-700 dark:text-gray-300">Mock Mode</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio" name="mode" value="typhoon"
              checked={mode === 'typhoon'} onChange={() => setMode('typhoon')}
              className="text-indigo-600"
            />
            <CpuChipIcon className="w-4 h-4 text-gray-500" />
            <span className="text-sm text-gray-700 dark:text-gray-300">Typhoon Mode</span>
          </label>
        </div>

        <button
          onClick={handleGenerate}
          disabled={!tseContent || running}
          className="flex items-center gap-2 px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-sm font-medium text-white transition-colors shadow-lg shadow-indigo-600/20"
        >
          <PlayIcon className="w-4 h-4" />
          {running ? 'Generating...' : 'Generate Tests'}
        </button>

        {downloadZip && (
          <a
            href={`/api/download-tests/${downloadZip}`}
            className="flex items-center gap-2 px-4 py-2.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-sm font-medium text-white transition-colors"
          >
            <ArrowDownTrayIcon className="w-4 h-4" />
            Download ZIP
          </a>
        )}
      </div>

      {/* Pipeline progress */}
      {Object.keys(stepStatuses).length > 0 && (
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <CodeBracketIcon className="w-4 h-4 text-indigo-500" />
            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-200">Pipeline Progress</h3>
          </div>
          <div className="flex items-center gap-1">
            {PIPELINE_STEPS.map((step, i) => {
              const status = stepStatuses[step.id] || 'pending'
              const colors = {
                pending: 'bg-gray-200 dark:bg-gray-700 text-gray-500',
                active: 'bg-indigo-100 dark:bg-indigo-500/20 text-indigo-600 dark:text-indigo-400 ring-2 ring-indigo-500',
                done: 'bg-emerald-100 dark:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400',
                error: 'bg-red-100 dark:bg-red-500/20 text-red-600 dark:text-red-400',
              }
              return (
                <div key={step.id} className="flex items-center gap-1 flex-1">
                  <div className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium flex-1 ${colors[status]}`}>
                    {status === 'done' && <CheckCircleIcon className="w-3.5 h-3.5" />}
                    {status === 'error' && <XCircleIcon className="w-3.5 h-3.5" />}
                    {status === 'active' && <span className="w-2 h-2 rounded-full bg-indigo-500 animate-pulse" />}
                    {step.label}
                  </div>
                  {i < PIPELINE_STEPS.length - 1 && (
                    <span className="text-gray-300 dark:text-gray-700 text-xs">{'>'}</span>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Events log */}
      {events.length > 0 && (
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-800">
            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-200">Pipeline Log</h3>
          </div>
          <div className="max-h-48 overflow-y-auto px-4 py-2 space-y-1 font-mono text-xs">
            {events.map((ev, i) => (
              <div key={i} className={`${
                ev.level === 'error' ? 'text-red-500' : ev.level === 'success' ? 'text-emerald-500 dark:text-emerald-400' : 'text-gray-500 dark:text-gray-400'
              }`}>
                {ev.node && <span className="font-semibold">[{ev.node}] </span>}
                {ev.msg}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Generated code preview */}
      {fileKeys.length > 0 && (
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-200 dark:border-gray-800">
            <DocumentTextIcon className="w-4 h-4 text-indigo-500" />
            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-200">Generated Code</h3>
            <span className="text-xs text-gray-400 ml-auto">{fileKeys.length} files</span>
          </div>

          {/* File tabs */}
          <div className="flex gap-0.5 px-2 pt-2 overflow-x-auto border-b border-gray-200 dark:border-gray-800">
            {fileKeys.map((key) => (
              <button
                key={key}
                onClick={() => setActiveFileTab(key)}
                className={`px-3 py-1.5 rounded-t text-xs font-medium transition-colors whitespace-nowrap ${
                  activeFileTab === key
                    ? 'bg-gray-100 dark:bg-gray-800 text-indigo-600 dark:text-indigo-400 border-b-2 border-indigo-500'
                    : 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
                }`}
              >
                {key}
              </button>
            ))}
          </div>

          {/* Code view */}
          <div className="overflow-auto max-h-[500px]">
            <pre className="p-4 text-xs text-gray-700 dark:text-gray-300 font-mono whitespace-pre-wrap leading-relaxed">
              {generatedFiles[activeFileTab] || ''}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}
