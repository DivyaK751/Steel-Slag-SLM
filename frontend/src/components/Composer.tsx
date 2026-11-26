import { useState, type FormEvent, type KeyboardEvent } from 'react'

interface ComposerProps {
  isDisabled: boolean
  onSend: (text: string) => void
}

/** The message box: Enter sends, Shift+Enter starts a new line. */
export function Composer({ isDisabled, onSend }: ComposerProps) {
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
        placeholder="Ask a question about steel slag…"
        rows={2}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={handleKeyDown}
      />
      <button type="submit" disabled={!canSend}>
        Send
      </button>
    </form>
  )
}
