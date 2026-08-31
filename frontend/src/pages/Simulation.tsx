import { useState, useEffect, useRef, useMemo } from 'react'
import {
  Play,
  Pause,
  RotateCcw,
  ChevronRight,
  ChevronLeft,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Shield,
  FileCode,
  Zap,
  Sliders,
  Check,
  Lock,
} from 'lucide-react'
import { api } from '../api'
import type { SimulationScenario, SimulationResult, SimulationStep } from '../api'
import { Badge } from '../components/Badge'
import { Panel } from '../components/Panel'
import { Loader, ErrorBox } from '../components/Loader'

type PlaybackMode = 'interactive' | 'stream' | 'instant'
type CategoryFilter = 'ALL' | 'LEGIT' | 'INTEGRITY' | 'MANDATE' | 'VELOCITY' | 'CONTENT' | 'CLEARING'

const STEP_COLORS: Record<string, { dot: string; text: string; bg: string; border: string }> = {
  PASS: { dot: 'bg-emerald-500', text: 'text-emerald-700', bg: 'bg-emerald-50', border: 'border-emerald-200' },
  ALLOW: { dot: 'bg-emerald-500', text: 'text-emerald-700', bg: 'bg-emerald-50', border: 'border-emerald-200' },
  MINTED: { dot: 'bg-purple-500', text: 'text-purple-700', bg: 'bg-purple-50', border: 'border-purple-200' },
  FAIL: { dot: 'bg-rose-500', text: 'text-rose-700', bg: 'bg-rose-50', border: 'border-rose-200' },
  DENY: { dot: 'bg-rose-500', text: 'text-rose-700', bg: 'bg-rose-50', border: 'border-rose-200' },
  REJECTED: { dot: 'bg-rose-400', text: 'text-rose-700', bg: 'bg-rose-50', border: 'border-rose-200' },
  QUARANTINE: { dot: 'bg-amber-500', text: 'text-amber-700', bg: 'bg-amber-50', border: 'border-amber-200' },
  STEP_UP: { dot: 'bg-sky-500', text: 'text-sky-700', bg: 'bg-sky-50', border: 'border-sky-200' },
  SKIPPED: { dot: 'bg-slate-300', text: 'text-slate-500', bg: 'bg-slate-50', border: 'border-slate-200' },
  EVALUATING: { dot: 'bg-indigo-500 animate-ping', text: 'text-indigo-700', bg: 'bg-indigo-50', border: 'border-indigo-300' },
  PENDING: { dot: 'bg-slate-200', text: 'text-slate-400', bg: 'bg-white', border: 'border-slate-200' },
}

export function SimulationPage() {
  const [scenarios, setScenarios] = useState<SimulationScenario[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Simulation controls state
  const [selectedScenarioId, setSelectedScenarioId] = useState<string>('legit_purchase')
  const [categoryFilter, setCategoryFilter] = useState<CategoryFilter>('ALL')
  const [playbackMode, setPlaybackMode] = useState<PlaybackMode>('stream')
  const [playbackSpeed, setPlaybackSpeed] = useState<number>(800) // ms per step

  // Custom parameters toggle
  const [showCustomParams, setShowCustomParams] = useState(false)
  const [customTier, setCustomTier] = useState<string>('')
  const [customAmount, setCustomAmount] = useState<string>('')

  // Execution state
  const [simResult, setSimResult] = useState<SimulationResult | null>(null)
  const [currentStepIndex, setCurrentStepIndex] = useState<number>(-1) // -1 = idle
  const [isPlaying, setIsPlaying] = useState(false)
  const [isExecuting, setIsExecuting] = useState(false)
  const [inspectingStepIndex, setInspectingStepIndex] = useState<number>(0)
  const [logs, setLogs] = useState<string[]>([])
  const [showRawJson, setShowRawJson] = useState(false)

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Fetch scenarios catalog on mount
  useEffect(() => {
    api
      .simulationScenarios()
      .then((data) => {
        setScenarios(data)
        if (data.length > 0) setSelectedScenarioId(data[0].scenario_id)
      })
      .catch((err) => setError(String(err)))
      .finally(() => setLoading(false))
  }, [])

  // Clear timers on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [])

  const filteredScenarios = useMemo(() => {
    if (categoryFilter === 'ALL') return scenarios
    return scenarios.filter((s) => s.category === categoryFilter)
  }, [scenarios, categoryFilter])

  const selectedScenario = useMemo(() => {
    return scenarios.find((s) => s.scenario_id === selectedScenarioId) || null
  }, [scenarios, selectedScenarioId])

  // Log append helper
  const appendLog = (msg: string) => {
    const ts = new Date().toISOString().substring(11, 23)
    setLogs((prev) => [...prev, `[${ts}] ${msg}`])
  }

  // Execute simulation from backend
  const startSimulation = async () => {
    if (isExecuting) return
    setIsExecuting(true)
    setIsPlaying(false)
    setCurrentStepIndex(-1)
    if (timerRef.current) clearInterval(timerRef.current)

    setLogs([])
    appendLog(`Initializing simulation run for scenario: '${selectedScenarioId}'...`)

    try {
      const customParams: Record<string, unknown> = {}
      if (customTier) customParams.tier = customTier
      if (customAmount) customParams.amount_inr = parseFloat(customAmount)

      const result = await api.runSimulation({
        scenario_id: selectedScenarioId,
        custom_params: Object.keys(customParams).length > 0 ? customParams : undefined,
      })

      setSimResult(result)
      appendLog(`Request generated: ${result.request_summary.method} ${result.request_summary.path} by agent '${result.request_summary.agent_id}' (${result.request_summary.tier})`)
      appendLog(`Cart total: ₹${result.request_summary.cart_total_inr.toFixed(2)} with ${result.request_summary.items.length} line item(s)`)

      if (playbackMode === 'instant') {
        setCurrentStepIndex(result.steps.length - 1)
        setInspectingStepIndex(result.steps.length - 1)
        result.steps.forEach((st) => {
          appendLog(`${st.step_id} (${st.name}) -> ${st.verdict} (${st.elapsed_ms.toFixed(2)}ms)${st.reason_codes.length ? ` [${st.reason_codes.join(', ')}]` : ''}`)
        })
        appendLog(`Simulation complete: Final decision ${result.decision} in ${result.total_latency_ms.toFixed(2)}ms`)
        setIsExecuting(false)
      } else if (playbackMode === 'stream') {
        setCurrentStepIndex(0)
        setInspectingStepIndex(0)
        setIsPlaying(true)
        runStreamPlayback(result, 0)
      } else {
        // Interactive mode: advance to step 0
        setCurrentStepIndex(0)
        setInspectingStepIndex(0)
        const first = result.steps[0]
        appendLog(`${first.step_id} (${first.name}) -> ${first.verdict} (${first.elapsed_ms.toFixed(2)}ms)`)
        setIsExecuting(false)
      }
    } catch (err) {
      appendLog(`ERROR: Simulation failed to execute: ${String(err)}`)
      setError(String(err))
      setIsExecuting(false)
    }
  }

  // Stream playback runner
  const runStreamPlayback = (result: SimulationResult, startIdx: number) => {
    let current = startIdx
    if (current < result.steps.length) {
      const step = result.steps[current]
      appendLog(`${step.step_id} (${step.name}) -> ${step.verdict} (${step.elapsed_ms.toFixed(2)}ms)${step.reason_codes.length ? ` [${step.reason_codes.join(', ')}]` : ''}`)
    }

    timerRef.current = setInterval(() => {
      current += 1
      if (current >= result.steps.length) {
        if (timerRef.current) clearInterval(timerRef.current)
        setIsPlaying(false)
        setIsExecuting(false)
        appendLog(`Simulation complete: Final decision ${result.decision} in ${result.total_latency_ms.toFixed(2)}ms`)
        if (result.obligation) {
          appendLog(`Obligation ${result.obligation.obligation_id} sealed to ledger tip: ${result.obligation.ledger_tip.substring(0, 16)}...`)
        }
      } else {
        setCurrentStepIndex(current)
        setInspectingStepIndex(current)
        const step = result.steps[current]
        appendLog(`${step.step_id} (${step.name}) -> ${step.verdict} (${step.elapsed_ms.toFixed(2)}ms)${step.reason_codes.length ? ` [${step.reason_codes.join(', ')}]` : ''}`)
      }
    }, playbackSpeed)
  }

  const handlePauseResume = () => {
    if (!simResult) return
    if (isPlaying) {
      if (timerRef.current) clearInterval(timerRef.current)
      setIsPlaying(false)
      appendLog(`Playback paused at step ${currentStepIndex + 1} of ${simResult.steps.length}`)
    } else {
      if (currentStepIndex >= simResult.steps.length - 1) {
        // Restart from 0
        setCurrentStepIndex(0)
        setInspectingStepIndex(0)
        setIsPlaying(true)
        runStreamPlayback(simResult, 0)
      } else {
        setIsPlaying(true)
        runStreamPlayback(simResult, currentStepIndex)
      }
    }
  }

  const handleStepNext = () => {
    if (!simResult) return
    if (currentStepIndex < simResult.steps.length - 1) {
      const next = currentStepIndex + 1
      setCurrentStepIndex(next)
      setInspectingStepIndex(next)
      const step = simResult.steps[next]
      appendLog(`${step.step_id} (${step.name}) -> ${step.verdict} (${step.elapsed_ms.toFixed(2)}ms)${step.reason_codes.length ? ` [${step.reason_codes.join(', ')}]` : ''}`)
      if (next === simResult.steps.length - 1) {
        appendLog(`Simulation complete: Final decision ${simResult.decision} in ${simResult.total_latency_ms.toFixed(2)}ms`)
      }
    }
  }

  const handleStepPrev = () => {
    if (!simResult || currentStepIndex <= 0) return
    const prev = currentStepIndex - 1
    setCurrentStepIndex(prev)
    setInspectingStepIndex(prev)
  }

  const handleReset = () => {
    if (timerRef.current) clearInterval(timerRef.current)
    setIsPlaying(false)
    setIsExecuting(false)
    setCurrentStepIndex(-1)
    setSimResult(null)
    setLogs([])
  }

  if (loading) return <Loader />
  if (error && !scenarios.length) return <ErrorBox message={error} />

  const activeInspectingStep: SimulationStep | null =
    simResult && inspectingStepIndex >= 0 && inspectingStepIndex < simResult.steps.length
      ? simResult.steps[inspectingStepIndex]
      : null

  const isComplete = simResult && currentStepIndex >= simResult.steps.length - 1

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <section className="flex justify-between gap-5 items-end max-md:flex-col max-md:items-start">
        <div>
          <p className="m-0 mb-1.5 text-ink-faint font-mono text-[11px] font-medium tracking-widest uppercase flex items-center gap-1.5">
            <Zap size={13} className="text-signal" />
            Interactive Gateway Sandbox
          </p>
          <h1 className="m-0 text-2xl font-semibold leading-tight tracking-tight">
            Real-Time Agent Simulation
          </h1>
        </div>
        <div className="flex items-center gap-3">
          <div className="rounded-sm px-3 py-1.5 font-mono text-xs font-semibold whitespace-nowrap bg-indigo-50 text-indigo-700 border border-indigo-200 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-indigo-500 animate-pulse" />
            Live Sandboxed Pipeline
          </div>
        </div>
      </section>

      {/* Scenario Selector & Filter Section */}
      <Panel
        title="1. Select Attack or Benign Scenario"
        meta={`${scenarios.length} evaluation scenarios available`}
      >
        <div className="p-4.5 space-y-4">
          {/* Category Filter Pills */}
          <div className="flex flex-wrap gap-2 pb-2 border-b border-line-soft">
            {(['ALL', 'LEGIT', 'INTEGRITY', 'MANDATE', 'VELOCITY', 'CONTENT', 'CLEARING'] as CategoryFilter[]).map(
              (cat) => (
                <button
                  key={cat}
                  onClick={() => setCategoryFilter(cat)}
                  className={`px-2.5 py-1 text-xs font-mono font-medium rounded transition-colors cursor-pointer ${
                    categoryFilter === cat
                      ? 'bg-chrome text-paper font-semibold'
                      : 'bg-paper text-ink-soft hover:bg-[#f1f3f6] border border-line-soft'
                  }`}
                >
                  {cat}
                </button>
              ),
            )}
          </div>

          {/* Scenario Grid */}
          <div className="grid grid-cols-3 max-lg:grid-cols-2 max-sm:grid-cols-1 gap-3 max-h-[300px] overflow-y-auto pr-1">
            {filteredScenarios.map((sc) => {
              const isSelected = sc.scenario_id === selectedScenarioId
              return (
                <button
                  key={sc.scenario_id}
                  onClick={() => {
                    setSelectedScenarioId(sc.scenario_id)
                    handleReset()
                  }}
                  className={`text-left p-3.5 rounded border transition-all cursor-pointer flex flex-col justify-between gap-2.5 ${
                    isSelected
                      ? 'border-signal bg-[#f6f7fd] ring-2 ring-signal/20'
                      : 'border-line bg-paper hover:border-signal/40 hover:bg-[#fafbfc]'
                  }`}
                >
                  <div>
                    <div className="flex items-center justify-between gap-2 mb-1.5">
                      <div className="flex items-center gap-1.5">
                        {sc.threat_class ? (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-rose-100 text-rose-800">
                            {sc.threat_class}
                          </span>
                        ) : (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-100 text-emerald-800">
                            LEGIT
                          </span>
                        )}
                        <span className="text-[10px] font-mono text-ink-faint">
                          Gate: {sc.target_gate}
                        </span>
                      </div>
                      <span
                        className={`text-[10px] font-mono font-bold uppercase px-1.5 py-0.5 rounded ${
                          sc.expected_decision === 'ALLOW'
                            ? 'bg-emerald-50 text-emerald-700'
                            : sc.expected_decision === 'QUARANTINE'
                            ? 'bg-amber-50 text-amber-700'
                            : sc.expected_decision === 'STEP_UP'
                            ? 'bg-sky-50 text-sky-700'
                            : 'bg-rose-50 text-rose-700'
                        }`}
                      >
                        {sc.expected_decision}
                      </span>
                    </div>
                    <h3 className="text-[13px] font-semibold m-0 text-ink line-clamp-1">
                      {sc.title}
                    </h3>
                    <p className="text-[11.5px] text-ink-soft m-0 mt-1 line-clamp-2 leading-relaxed">
                      {sc.summary}
                    </p>
                  </div>

                  <div className="flex items-center justify-between text-[11px] font-mono text-ink-faint pt-2 border-t border-line-soft">
                    <span>Tier: {sc.default_tier}</span>
                    <span>Amount: ₹{sc.default_amount_inr.toLocaleString('en-IN')}</span>
                  </div>
                </button>
              )
            })}
          </div>

          {/* Custom Parameters Form Toggle */}
          <div className="pt-2 border-t border-line-soft">
            <button
              onClick={() => setShowCustomParams(!showCustomParams)}
              className="inline-flex items-center gap-1.5 text-xs font-mono font-medium text-ink-soft hover:text-signal cursor-pointer"
            >
              <Sliders size={13} />
              {showCustomParams ? 'Hide custom parameters' : 'Customize request parameters (Tier, Amount)'}
            </button>

            {showCustomParams && (
              <div className="mt-3 p-3 bg-[#f8f9fb] border border-line-soft rounded grid grid-cols-2 max-sm:grid-cols-1 gap-4">
                <div>
                  <label className="block text-[11px] font-mono font-medium text-ink-soft uppercase mb-1">
                    Agent Trust Tier (Override)
                  </label>
                  <select
                    value={customTier}
                    onChange={(e) => setCustomTier(e.target.value)}
                    className="w-full bg-paper border border-line rounded px-2.5 py-1.5 text-xs font-mono text-ink"
                  >
                    <option value="">Default ({selectedScenario?.default_tier ?? 'Auto'})</option>
                    <option value="T0">T0 (Unknown / First contact)</option>
                    <option value="T1">T1 (Seen)</option>
                    <option value="T2">T2 (Established)</option>
                    <option value="T3">T3 (High trust)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-[11px] font-mono font-medium text-ink-soft uppercase mb-1">
                    Cart Amount in INR (Override)
                  </label>
                  <input
                    type="number"
                    placeholder={`Default: ₹${selectedScenario?.default_amount_inr ?? 2499}`}
                    value={customAmount}
                    onChange={(e) => setCustomAmount(e.target.value)}
                    className="w-full bg-paper border border-line rounded px-2.5 py-1.5 text-xs font-mono text-ink"
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </Panel>

      {/* Playback & Control Bar */}
      <section className="bg-paper border border-line rounded-md p-4 flex flex-wrap items-center justify-between gap-4">
        {/* Left: Action Buttons */}
        <div className="flex items-center gap-2">
          {!simResult || currentStepIndex === -1 ? (
            <button
              onClick={startSimulation}
              disabled={isExecuting}
              className="inline-flex items-center gap-2 px-4 py-2 bg-signal hover:bg-signal/90 text-white rounded font-medium text-xs tracking-wide uppercase transition-colors cursor-pointer disabled:opacity-50"
            >
              <Play size={14} />
              Run Simulation
            </button>
          ) : (
            <>
              {playbackMode === 'stream' && (
                <button
                  onClick={handlePauseResume}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-chrome text-paper hover:bg-chrome-line rounded font-medium text-xs cursor-pointer"
                >
                  {isPlaying ? <Pause size={14} /> : <Play size={14} />}
                  {isPlaying ? 'Pause' : 'Resume'}
                </button>
              )}

              <button
                onClick={handleStepPrev}
                disabled={currentStepIndex <= 0 || isPlaying}
                className="inline-flex items-center gap-1 px-2.5 py-1.5 bg-paper border border-line hover:bg-[#f1f3f6] rounded text-xs font-medium cursor-pointer disabled:opacity-40"
                title="Previous Gate"
              >
                <ChevronLeft size={14} />
                Prev
              </button>

              <button
                onClick={handleStepNext}
                disabled={!simResult || currentStepIndex >= simResult.steps.length - 1 || isPlaying}
                className="inline-flex items-center gap-1 px-3 py-1.5 bg-chrome text-paper hover:bg-chrome-line rounded text-xs font-medium cursor-pointer disabled:opacity-40"
                title="Next Gate"
              >
                Next Gate
                <ChevronRight size={14} />
              </button>

              <button
                onClick={handleReset}
                className="inline-flex items-center gap-1 px-2.5 py-1.5 bg-paper border border-line hover:bg-rose-50 text-rose-700 rounded text-xs font-medium cursor-pointer"
                title="Reset simulation"
              >
                <RotateCcw size={13} />
                Reset
              </button>
            </>
          )}
        </div>

        {/* Middle: Mode Switcher */}
        <div className="flex items-center gap-3">
          <span className="text-[11px] font-mono uppercase text-ink-faint font-semibold">Mode:</span>
          <div className="inline-flex rounded border border-line bg-paper p-0.5">
            {(['stream', 'interactive', 'instant'] as PlaybackMode[]).map((mode) => (
              <button
                key={mode}
                onClick={() => {
                  setPlaybackMode(mode)
                  if (isPlaying && timerRef.current) {
                    clearInterval(timerRef.current)
                    setIsPlaying(false)
                  }
                }}
                className={`px-2.5 py-1 text-[11px] font-mono font-medium rounded transition-colors cursor-pointer ${
                  playbackMode === mode
                    ? 'bg-chrome text-paper font-semibold'
                    : 'text-ink-soft hover:text-ink'
                }`}
              >
                {mode === 'stream' ? 'Auto Stream' : mode === 'interactive' ? 'Step-by-Step' : 'Instant'}
              </button>
            ))}
          </div>

          {playbackMode === 'stream' && (
            <select
              value={playbackSpeed}
              onChange={(e) => setPlaybackSpeed(Number(e.target.value))}
              className="bg-paper border border-line rounded px-2 py-1 text-xs font-mono text-ink"
            >
              <option value={400}>Fast (400ms)</option>
              <option value={800}>Normal (800ms)</option>
              <option value={1500}>Slow (1.5s)</option>
              <option value={2500}>Step Demo (2.5s)</option>
            </select>
          )}
        </div>

        {/* Right: Active Status Indicator */}
        <div className="text-right font-mono text-xs text-ink-soft">
          {simResult ? (
            <span>
              Step {Math.max(0, currentStepIndex + 1)} / {simResult.steps.length}
              {isComplete && <span className="ml-2 font-bold text-signal">• Finished</span>}
            </span>
          ) : (
            <span className="text-ink-faint">Ready to simulate</span>
          )}
        </div>
      </section>

      {/* Live Pipeline Stepper Diagram */}
      {simResult && (
        <Panel
          title="2. Live Evaluation Pipeline"
          meta={
            isComplete
              ? `Completed in ${simResult.total_latency_ms.toFixed(2)} ms`
              : currentStepIndex >= 0
              ? `Evaluating step ${currentStepIndex + 1} of ${simResult.steps.length}...`
              : 'Pipeline ready'
          }
        >
          <div className="p-4.5 overflow-x-auto">
            <ol className="list-none m-0 p-0 flex items-center justify-between min-w-[760px] gap-2">
              {simResult.steps.map((step, idx) => {
                const isPast = idx <= currentStepIndex
                const isCurrent = idx === currentStepIndex
                const isSelectedForInspect = idx === inspectingStepIndex

                const stepVerdict = isPast ? step.verdict : 'PENDING'
                const styling = STEP_COLORS[stepVerdict] || STEP_COLORS.PENDING

                return (
                  <li
                    key={step.step_id}
                    onClick={() => {
                      if (idx <= currentStepIndex) setInspectingStepIndex(idx)
                    }}
                    className={`flex-1 relative flex flex-col items-center p-3 rounded-lg border text-center transition-all ${
                      idx <= currentStepIndex ? 'cursor-pointer' : 'cursor-not-allowed opacity-50'
                    } ${
                      isSelectedForInspect
                        ? 'ring-2 ring-signal border-signal bg-signal/5'
                        : styling.bg + ' ' + styling.border
                    }`}
                  >
                    {/* Connecting line */}
                    {idx < simResult.steps.length - 1 && (
                      <span
                        className={`absolute top-[28px] left-[calc(50%+24px)] h-0.5 w-[calc(100%-48px)] z-0 ${
                          idx < currentStepIndex ? 'bg-signal' : 'bg-line border-dashed'
                        }`}
                        aria-hidden
                      />
                    )}

                    {/* Step Icon / Dot */}
                    <div
                      className={`w-7 h-7 rounded-full flex items-center justify-center font-mono text-xs font-bold text-white z-10 mb-2 transition-transform ${
                        styling.dot
                      } ${isCurrent && isPlaying ? 'scale-110 shadow-lg' : ''}`}
                    >
                      {stepVerdict === 'PASS' || stepVerdict === 'ALLOW' || stepVerdict === 'MINTED' ? (
                        <Check size={14} />
                      ) : stepVerdict === 'FAIL' || stepVerdict === 'DENY' || stepVerdict === 'REJECTED' ? (
                        <XCircle size={14} />
                      ) : stepVerdict === 'QUARANTINE' ? (
                        <AlertTriangle size={14} />
                      ) : (
                        idx + 1
                      )}
                    </div>

                    <span className="font-mono text-xs font-bold text-ink">
                      {step.step_id}
                    </span>
                    <span className="text-[11px] font-medium text-ink-soft line-clamp-1 mb-1">
                      {step.name.split(' ')[0]}
                    </span>

                    {/* Verdict Pill */}
                    <span
                      className={`text-[9.5px] font-mono font-bold uppercase px-1.5 py-0.5 rounded border ${
                        styling.bg
                      } ${styling.text} ${styling.border}`}
                    >
                      {isPast ? step.verdict : 'WAITING'}
                    </span>

                    {isPast && (
                      <span className="font-mono text-[9.5px] text-ink-faint mt-1">
                        {step.elapsed_ms.toFixed(2)} ms
                      </span>
                    )}
                  </li>
                )
              })}
            </ol>
          </div>
        </Panel>
      )}

      {/* Deep Step Inspector & Execution Terminal Grid */}
      {simResult && activeInspectingStep && (
        <div className="grid grid-cols-2 max-lg:grid-cols-1 gap-5">
          {/* Left: Granular Step Inspector */}
          <Panel
            title={`3. Step Diagnostics: ${activeInspectingStep.step_id} — ${activeInspectingStep.name}`}
            meta={`${activeInspectingStep.elapsed_ms.toFixed(2)} ms`}
          >
            <div className="p-4.5 space-y-4">
              {/* Verdict Header Bar */}
              <div
                className={`p-3 rounded border flex items-center justify-between gap-3 ${
                  STEP_COLORS[activeInspectingStep.verdict]?.bg || 'bg-paper'
                } ${STEP_COLORS[activeInspectingStep.verdict]?.border || 'border-line'}`}
              >
                <div>
                  <p className="text-[11px] font-mono uppercase tracking-wide text-ink-soft m-0">
                    {activeInspectingStep.description}
                  </p>
                  <p className="text-xs font-medium text-ink m-0 mt-1">
                    {activeInspectingStep.explanation}
                  </p>
                </div>
                <div className="shrink-0">
                  <Badge value={activeInspectingStep.verdict} />
                </div>
              </div>

              {/* Assertion Checklist */}
              <div>
                <h4 className="text-xs font-mono font-semibold uppercase tracking-wider text-ink-faint mb-2">
                  Evaluated Assertions ({activeInspectingStep.assertions.length})
                </h4>
                <ul className="list-none m-0 p-0 space-y-2">
                  {activeInspectingStep.assertions.map((assertion, aIdx) => (
                    <li
                      key={aIdx}
                      className={`p-2.5 rounded border flex items-start gap-2.5 text-xs ${
                        assertion.passed
                          ? 'bg-emerald-50/50 border-emerald-100 text-emerald-900'
                          : 'bg-rose-50/50 border-rose-100 text-rose-900'
                      }`}
                    >
                      <div className="shrink-0 mt-0.5">
                        {assertion.passed ? (
                          <CheckCircle2 size={15} className="text-emerald-600" />
                        ) : (
                          <XCircle size={15} className="text-rose-600" />
                        )}
                      </div>
                      <div className="flex-1">
                        <span className="font-semibold">{assertion.check}:</span>{' '}
                        <span className="font-mono text-[11.5px] opacity-90">{assertion.detail}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>

              {/* Step Metadata & Reason Codes */}
              {activeInspectingStep.reason_codes.length > 0 && (
                <div className="p-3 bg-rose-50 border border-rose-200 rounded">
                  <span className="text-[11px] font-mono font-bold uppercase text-rose-800 block mb-1">
                    Citing Reason Codes:
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {activeInspectingStep.reason_codes.map((code) => (
                      <span
                        key={code}
                        className="px-2 py-0.5 bg-rose-100 border border-rose-300 text-rose-900 font-mono text-xs font-bold rounded"
                      >
                        {code}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Step Diagnostics JSON Drawer */}
              {Object.keys(activeInspectingStep.metadata).length > 0 && (
                <div>
                  <h4 className="text-xs font-mono font-semibold uppercase tracking-wider text-ink-faint mb-1.5">
                    Context Metadata
                  </h4>
                  <pre className="m-0 p-3 bg-[#1e222b] text-[#e2e8f0] rounded text-[11px] font-mono overflow-x-auto">
                    {JSON.stringify(activeInspectingStep.metadata, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </Panel>

          {/* Right: Live Execution Console / Streaming Logs */}
          <Panel
            title="Gateway Execution Terminal"
            meta={`${logs.length} events logged`}
          >
            <div className="p-4.5 flex flex-col h-[400px]">
              <div className="flex-1 bg-[#10141d] border border-[#232936] rounded-md p-3 font-mono text-[11.5px] text-emerald-400 overflow-y-auto space-y-1">
                <div className="text-slate-500 pb-1 border-b border-slate-800 flex items-center justify-between">
                  <span>KYA/control-plane-sim v0.1.0</span>
                  <span className="text-slate-600">RFC 9421 / AP2 Guard</span>
                </div>
                {logs.length === 0 ? (
                  <p className="text-slate-600 italic mt-2">
                    Press "Run Simulation" to start trace streaming...
                  </p>
                ) : (
                  logs.map((log, lIdx) => (
                    <div
                      key={lIdx}
                      className={
                        log.includes('FAIL') || log.includes('DENY') || log.includes('ERROR')
                          ? 'text-rose-400 font-medium'
                          : log.includes('QUARANTINE')
                          ? 'text-amber-400'
                          : log.includes('STEP_UP')
                          ? 'text-sky-400'
                          : log.includes('MINTED') || log.includes('complete')
                          ? 'text-purple-300 font-semibold'
                          : 'text-emerald-400'
                      }
                    >
                      {log}
                    </div>
                  ))
                )}
              </div>
            </div>
          </Panel>
        </div>
      )}

      {/* Final Outcome & Sealed Obligation Card */}
      {isComplete && simResult && (
        <section className="bg-paper border-2 border-line rounded-lg p-6 space-y-5 animate-[rise_200ms_ease-out]">
          <div className="flex justify-between items-center max-md:flex-col max-md:items-start gap-4 pb-4 border-b border-line-soft">
            <div>
              <p className="m-0 text-ink-faint font-mono text-[11px] font-medium tracking-widest uppercase">
                Final Adjudication Outcome
              </p>
              <h2 className="m-0 text-xl font-bold tracking-tight text-ink mt-0.5">
                {simResult.scenario_title}
              </h2>
            </div>
            <div className="flex items-center gap-3">
              <Badge value={simResult.decision} />
            </div>
          </div>

          <p className="m-0 text-sm text-ink-soft leading-relaxed">
            {simResult.explanation}
          </p>

          {/* Minted Obligation Receipt Banner */}
          {simResult.obligation ? (
            <div className="p-4.5 bg-gradient-to-br from-purple-50 to-indigo-50 border border-purple-200 rounded-md space-y-3">
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs font-bold uppercase tracking-wider text-purple-900 flex items-center gap-1.5">
                  <Shield size={15} className="text-purple-700" />
                  Sealed Obligation Receipt Minted
                </span>
                <span className="px-2 py-0.5 rounded text-[10.5px] font-mono font-bold bg-purple-200 text-purple-900">
                  {simResult.obligation.rail_type}
                </span>
              </div>

              <div className="grid grid-cols-3 max-md:grid-cols-1 gap-3 font-mono text-xs">
                <div className="p-2.5 bg-white/80 rounded border border-purple-100">
                  <span className="text-slate-500 block text-[10px] uppercase">Obligation ID</span>
                  <span className="font-semibold text-purple-950 font-mono text-[12px]">
                    {simResult.obligation.obligation_id}
                  </span>
                </div>
                <div className="p-2.5 bg-white/80 rounded border border-purple-100">
                  <span className="text-slate-500 block text-[10px] uppercase">Amount Due</span>
                  <span className="font-semibold text-purple-950 font-mono text-[12px]">
                    ₹{simResult.obligation.amount_due_inr.toFixed(2)} {simResult.obligation.currency}
                  </span>
                </div>
                <div className="p-2.5 bg-white/80 rounded border border-purple-100">
                  <span className="text-slate-500 block text-[10px] uppercase">Ledger Entries</span>
                  <span className="font-semibold text-purple-950 font-mono text-[12px]">
                    {simResult.obligation.ledger_entries} chained
                  </span>
                </div>
              </div>

              <div className="pt-2 border-t border-purple-200/60 font-mono text-[11px] text-purple-950 flex flex-wrap justify-between gap-2">
                <span>Receipt Hash: <span className="font-semibold">{simResult.obligation.receipt_hash.substring(0, 24)}...</span></span>
                <span>Ledger Tip: <span className="font-semibold">{simResult.obligation.ledger_tip.substring(0, 24)}...</span></span>
              </div>
            </div>
          ) : (
            <div className="p-3 bg-slate-50 border border-slate-200 rounded text-xs text-slate-600 font-mono flex items-center gap-2">
              <Lock size={14} className="text-slate-400" />
              Obligation minting blocked. Zero funds moved or allocated on payment rails.
            </div>
          )}

          {/* Raw Request Payload Toggle */}
          <div className="pt-2">
            <button
              onClick={() => setShowRawJson(!showRawJson)}
              className="inline-flex items-center gap-1.5 text-xs font-mono font-medium text-signal hover:underline cursor-pointer"
            >
              <FileCode size={13} />
              {showRawJson ? 'Hide raw RFC 9421 request payload' : 'Inspect raw signed HTTP request & RFC 9421 headers'}
            </button>

            {showRawJson && (
              <pre className="mt-3 p-4 bg-[#1e222b] text-[#e2e8f0] rounded text-[11px] font-mono overflow-x-auto max-h-[300px]">
                {JSON.stringify(simResult.raw_request, null, 2)}
              </pre>
            )}
          </div>
        </section>
      )}
    </div>
  )
}
