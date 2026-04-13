import { useState } from 'react'
import { PlayIcon, StopIcon } from '@heroicons/react/24/solid'
import { SparklesIcon } from '@heroicons/react/24/outline'

const EXAMPLES = [
  'Verify BMS overvoltage protection at 4.2V with 100ms response',
  'Test ESS inverter islanding detection under 200ms',
  'Validate DC-DC converter output regulation at +/-5% load step'
]

export default function GoalInput({ onRun, onStop, isRunning }) {
  const [goal, setGoal] = useState('')

  function handleSubmit(e) {
    e.preventDefault()
    if (!goal.trim() || isRunning) return
    onRun(goal.trim())
  }

  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5 shadow-xl">
      <div className="flex items-center gap-2 mb-3">
        <SparklesIcon className="w-4 h-4 text-indigo-400" />
        <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-200 tracking-tight">Test Goal</h2>
        <span className="ml-auto text-xs text-gray-400 dark:text-gray-600">Natural language input</span>
      </div>

      <form onSubmit={handleSubmit} className="flex gap-3">
        <input
          type="text"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="e.g. Verify BMS overvoltage protection at 4.2V with 100ms response"
          disabled={isRunning}
          className="flex-1 bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg px-4 py-2.5 text-sm text-gray-800 dark:text-gray-200 placeholder-gray-400 dark:placeholder-gray-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/50 disabled:opacity-50 transition-colors"
        />
        {isRunning ? (
          <button
            type="button"
            onClick={onStop}
            className="flex items-center gap-2 px-5 py-2.5 bg-red-600 hover:bg-red-500 rounded-lg text-sm font-medium text-white transition-colors shadow-lg shadow-red-600/20"
          >
            <StopIcon className="w-4 h-4" />
            Stop
          </button>
        ) : (
          <button
            type="submit"
            disabled={!goal.trim()}
            className="flex items-center gap-2 px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-sm font-medium text-white transition-colors shadow-lg shadow-indigo-600/20"
          >
            <PlayIcon className="w-4 h-4" />
            Run
          </button>
        )}
      </form>

      <div className="flex gap-4 mt-3 flex-wrap">
        <span className="text-xs text-gray-400 dark:text-gray-600">Examples:</span>
        {EXAMPLES.map((eg, i) => (
          <button
            key={i}
            type="button"
            onClick={() => !isRunning && setGoal(eg)}
            disabled={isRunning}
            className="text-xs text-gray-500 hover:text-indigo-400 transition-colors disabled:opacity-40 disabled:cursor-not-allowed truncate max-w-[280px]"
            title={eg}
          >
            {eg.length > 48 ? eg.slice(0, 48) + '...' : eg}
          </button>
        ))}
      </div>
    </div>
  )
}
