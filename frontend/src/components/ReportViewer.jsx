import { useState, useEffect } from 'react'
import {
  DocumentTextIcon,
  ArrowPathIcon,
  DocumentArrowDownIcon
} from '@heroicons/react/24/outline'

export default function ReportViewer() {
  const [reports, setReports] = useState([])
  const [loading, setLoading] = useState(false)
  const [selectedReport, setSelectedReport] = useState(null)
  const [reportHtml, setReportHtml] = useState('')
  const [error, setError] = useState(null)

  async function fetchReports() {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/api/reports')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setReports(data)
      if (data.length > 0 && !selectedReport) {
        loadReport(data[0].filename)
      }
    } catch (err) {
      setError('Could not load reports. Is the backend running?')
      setReports([])
    } finally {
      setLoading(false)
    }
  }

  async function loadReport(filename) {
    setSelectedReport(filename)
    setReportHtml('')
    try {
      const res = await fetch(`/api/reports/${encodeURIComponent(filename)}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const html = await res.text()
      setReportHtml(html)
    } catch (err) {
      setReportHtml(`<p style="padding:2rem;color:#ef4444;">Failed to load report: ${err.message}</p>`)
    }
  }

  useEffect(() => {
    fetchReports()
  }, [])

  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl shadow-xl overflow-hidden">
      <div className="flex" style={{ minHeight: '600px' }}>
        {/* Report list sidebar */}
        <div className="w-64 flex-shrink-0 border-r border-gray-200 dark:border-gray-800 flex flex-col">
          <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <DocumentTextIcon className="w-4 h-4 text-indigo-500" />
              <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-200">Reports</h2>
            </div>
            <button
              onClick={fetchReports}
              disabled={loading}
              className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
              title="Refresh"
            >
              <ArrowPathIcon className={`w-4 h-4 text-gray-400 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto">
            {error && (
              <div className="px-4 py-8 text-center">
                <p className="text-xs text-red-500">{error}</p>
              </div>
            )}
            {!error && reports.length === 0 && !loading && (
              <div className="px-4 py-8 text-center">
                <DocumentArrowDownIcon className="w-8 h-8 text-gray-300 dark:text-gray-700 mx-auto mb-2" />
                <p className="text-xs text-gray-400 dark:text-gray-600">No reports generated yet</p>
              </div>
            )}
            {reports.map((r) => (
              <button
                key={r.filename}
                onClick={() => loadReport(r.filename)}
                className={`w-full text-left px-4 py-3 border-b border-gray-100 dark:border-gray-800/50 transition-colors ${
                  selectedReport === r.filename
                    ? 'bg-indigo-50 dark:bg-indigo-500/10'
                    : 'hover:bg-gray-50 dark:hover:bg-gray-800/30'
                }`}
              >
                <p className="text-xs font-medium text-gray-800 dark:text-gray-200 truncate">{r.filename}</p>
                <div className="flex items-center gap-2 mt-1">
                  {r.timestamp && (
                    <span className="text-xs text-gray-400">{r.timestamp}</span>
                  )}
                  {r.size_bytes != null && (
                    <span className="text-xs text-gray-400">{(r.size_bytes / 1024).toFixed(1)} KB</span>
                  )}
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Report content */}
        <div className="flex-1 flex flex-col">
          {selectedReport ? (
            <>
              <div className="px-4 py-2 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between bg-gray-50 dark:bg-gray-800/30">
                <span className="text-xs font-mono text-gray-500">{selectedReport}</span>
              </div>
              <div className="flex-1">
                {reportHtml ? (
                  <iframe
                    srcDoc={reportHtml}
                    title="Test Report"
                    className="w-full h-full border-0"
                    style={{ minHeight: '550px' }}
                    sandbox="allow-same-origin"
                  />
                ) : (
                  <div className="flex items-center justify-center h-full">
                    <ArrowPathIcon className="w-6 h-6 text-gray-300 dark:text-gray-700 animate-spin" />
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center gap-2">
              <DocumentTextIcon className="w-12 h-12 text-gray-200 dark:text-gray-700" />
              <p className="text-sm text-gray-400 dark:text-gray-600">Select a report to view</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
