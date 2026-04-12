import { useState, useRef, useCallback } from 'react'

/**
 * useSSE — POST /api/run { goal } => SSE stream
 *
 * The backend returns a streaming HTTP response in SSE format:
 *
 *   event: thought
 *   data: {"node":"plan_tests","message":"...","data":{...},"timestamp":"..."}
 *
 *   event: action
 *   data: {"node":"execute_scenario","message":"...","data":{...},"timestamp":"..."}
 *
 * Supported event types:
 *   thought | action | observation | result | plan | diagnosis | report | error
 *
 * Usage:
 *   const { start, stop, isRunning } = useSSE({
 *     onEvent(type, event)  { ... },   // every incoming event
 *     onDone()              { ... },   // stream ended cleanly
 *     onError(event)        { ... },   // HTTP error or event type === 'error'
 *   })
 *
 *   start('BMS overvoltage protection at 4.2V with 100ms response')
 *   stop()
 *
 * Notes:
 *   - EventSource only supports GET; we use fetch + ReadableStream for POST SSE.
 *   - Calling start() while running aborts the previous request first.
 *   - Callbacks are kept in a ref so they can change between renders without
 *     causing start/stop to be recreated.
 */

// ---------------------------------------------------------------------------
// SSE text parser
// ---------------------------------------------------------------------------

/**
 * Parse accumulated SSE text into complete events.
 * Returns { events: Array<{type, payload}>, remaining: string }
 *
 * SSE spec: events are separated by blank lines (\n\n).
 * Each line inside a block is either "field: value" or a comment (": ...").
 * Multi-line data is joined with \n before JSON.parse.
 */
function parseSseBuffer(buffer) {
  const events = []
  // Split on double newline — handles \r\n and \n line endings
  const blocks = buffer.split(/\n\n|\r\n\r\n/)
  const remaining = blocks.pop()   // last block may be incomplete

  for (const block of blocks) {
    if (!block.trim()) continue

    let type = 'message'
    const dataLines = []

    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith('event:')) {
        type = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart())
      }
      // ignore id:, retry:, comments
    }

    const raw = dataLines.join('\n')
    if (!raw) continue

    let payload
    try {
      payload = JSON.parse(raw)
    } catch {
      payload = { node: 'system', message: raw, data: null, timestamp: new Date().toISOString() }
    }

    events.push({ type, payload })
  }

  return { events, remaining: remaining ?? '' }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useSSE({ onEvent, onDone, onError } = {}) {
  const [isRunning, setIsRunning] = useState(false)

  // Keep callbacks in a ref so start/stop never need to be recreated
  const cbRef = useRef({ onEvent, onDone, onError })
  cbRef.current = { onEvent, onDone, onError }

  // AbortController for the current fetch
  const abortRef = useRef(null)

  // -------------------------------------------------------------------------
  const start = useCallback(async (goal) => {
    // Cancel any in-flight request
    abortRef.current?.abort()

    const ctrl = new AbortController()
    abortRef.current = ctrl
    setIsRunning(true)

    try {
      const res = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal }),
        signal: ctrl.signal,
      })

      if (!res.ok) {
        const body = await res.text().catch(() => '')
        cbRef.current.onError?.({
          node: 'system',
          message: body || `HTTP ${res.status} ${res.statusText}`,
          data: null,
          timestamp: new Date().toISOString(),
        })
        return
      }

      // Stream the response body line by line
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        const { events, remaining } = parseSseBuffer(buffer)
        buffer = remaining

        for (const { type, payload } of events) {
          cbRef.current.onEvent?.(type, payload)

          // 'result' is the terminal success event; 'error' is terminal failure
          if (type === 'error') {
            cbRef.current.onError?.(payload)
          }
        }
      }

      // Stream ended cleanly
      if (!ctrl.signal.aborted) {
        cbRef.current.onDone?.()
      }

    } catch (err) {
      if (err.name === 'AbortError') return   // intentional stop()
      cbRef.current.onError?.({
        node: 'system',
        message: err.message,
        data: null,
        timestamp: new Date().toISOString(),
      })
    } finally {
      // Only reset running state if this controller is still the current one
      // (guards against a race where stop()+start() was called mid-flight)
      if (abortRef.current === ctrl) {
        setIsRunning(false)
      }
    }
  }, [])

  // -------------------------------------------------------------------------
  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setIsRunning(false)
  }, [])

  return { start, stop, isRunning }
}

// ---------------------------------------------------------------------------
// Event type metadata (useful for UI rendering)
// ---------------------------------------------------------------------------

export const SSE_EVENT_META = {
  thought:     { level: 'info',    label: 'Thought'     },
  plan:        { level: 'info',    label: 'Plan'        },
  action:      { level: 'info',    label: 'Action'      },
  observation: { level: 'info',    label: 'Observation' },
  diagnosis:   { level: 'warn',    label: 'Diagnosis'   },
  result:      { level: 'success', label: 'Result'      },
  report:      { level: 'success', label: 'Report'      },
  error:       { level: 'error',   label: 'Error'       },
}
