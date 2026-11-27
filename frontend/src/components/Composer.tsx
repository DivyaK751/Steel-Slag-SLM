import { useState, type FormEvent, type KeyboardEvent } from 'react'

import type { ModelInfo } from '../api/types'
import { ArrowUpIcon, BrainIcon, ChevronDownIcon, PaperclipIcon } from './Icons'

const THINKING_OFF_HINT = 'Only Qwen can switch thinking; Gemini always reasons internally'

interface ComposerProps {
  isDisabled: boolean
  models: ModelInfo[]
  readerId: string
  writerId: string
  isThinking: boolean
  canThink: boolean // a picked model can switch thinking (Qwen)
  onReaderChange: (modelId: string) => void
  onWriterChange: (modelId: string) => void
  onThinkingChange: (isThinking: boolean) => void
  onSend: (text: string) => void
}

interface ModelSelectProps {
  job: string
  hint: string
  models: ModelInfo[]
  value: string
  onChange: (modelId: string) => void
}

/** One compact model menu, named for its job (Reader or Writer). */
function ModelSelect({ job, hint, models, value, onChange }: ModelSelectProps) {
  return (
    <label className="model-select" title={hint}>
      <span className="model-select-job">{job}</span>
      <select aria-label={`${job} model`} value={value} onChange={(event) => onChange(event.target.value)}>
        {models.map((model) => (
          <option key={model.id} value={model.id}>
            {model.label}
          </option>
        ))}
      </select>
      <ChevronDownIcon />
    </label>
  )
}

/** The message box: Enter sends, Shift+Enter starts a new line; the models and thinking sit below. */
export function Composer(props: ComposerProps) {
  const { isDisabled, models, readerId, writerId, isThinking, canThink } = props
  const { onReaderChange, onWriterChange, onThinkingChange, onSend } = props
  const [text, setText] = useState('')
  const canSend = !isDisabled && text.trim() !== ''

  function submit(event?: FormEvent) {
    event?.preventDefault()
    if (!canSend) return
    onSend(text.trim())
    setText('')
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // isComposing: Enter that confirms an IME candidate (e.g. Chinese input) must not send
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) submit(event)
  }

  return (
    <form className="composer" onSubmit={submit}>
      <textarea
        aria-label="Message"
        placeholder="Ask about slag chemistry, carbonation data or papers…"
        rows={2}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={handleKeyDown}
      />
      <div className="composer-bar">
        <button type="button" className="icon-button" aria-label="Attach file" title="Coming soon" disabled>
          <PaperclipIcon />
        </button>
        <ModelSelect
          job="Reader"
          hint="Reads your question and picks what to search"
          models={models}
          value={readerId}
          onChange={onReaderChange}
        />
        <ModelSelect
          job="Writer"
          hint="Writes the answer from the evidence"
          models={models}
          value={writerId}
          onChange={onWriterChange}
        />
        <button
          type="button"
          role="switch"
          className="thinking-switch"
          aria-checked={isThinking}
          disabled={!canThink}
          title={canThink ? 'Let Qwen reason before it answers' : THINKING_OFF_HINT}
          onClick={() => onThinkingChange(!isThinking)}
        >
          <BrainIcon /> Thinking
          <span className="switch-track" aria-hidden="true">
            <span className="switch-knob" />
          </span>
        </button>
        <button type="submit" className="send-button" aria-label="Send" disabled={!canSend}>
          <ArrowUpIcon />
        </button>
      </div>
    </form>
  )
}
