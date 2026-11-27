import { useState, type FormEvent, type KeyboardEvent } from 'react'

import { MODELS } from '../placeholder/sampleData'
import { ArrowUpIcon, BrainIcon, ChevronDownIcon, PaperclipIcon } from './Icons'

interface ComposerProps {
  isDisabled: boolean
  modelId: string
  isThinking: boolean
  onModelChange: (modelId: string) => void
  onThinkingChange: (isThinking: boolean) => void
  onSend: (text: string) => void
}

/** The message box: Enter sends, Shift+Enter starts a new line; model and thinking sit below. */
export function Composer(props: ComposerProps) {
  const { isDisabled, modelId, isThinking, onModelChange, onThinkingChange, onSend } = props
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
        <label className="model-select">
          <select aria-label="Model" value={modelId} onChange={(event) => onModelChange(event.target.value)}>
            {MODELS.map((model) => (
              <option key={model.id} value={model.id}>
                {model.label}
              </option>
            ))}
          </select>
          <ChevronDownIcon />
        </label>
        <button
          type="button"
          role="switch"
          className="thinking-switch"
          aria-checked={isThinking}
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
