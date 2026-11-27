import type { ChatEvent, ModelChoice } from './types'

const EVENT_SEPARATOR = '\n\n'
const DATA_PREFIX = 'data: '

export const STREAM_CUT_MESSAGE = 'The answer stopped before it finished. Please try again.'

/** Split buffered stream text into complete events, keeping any partial event as `rest`. */
export function parseSse(buffer: string): { events: ChatEvent[]; rest: string } {
  const blocks = buffer.replaceAll('\r\n', '\n').split(EVENT_SEPARATOR)
  const rest = blocks.pop() ?? '' // the text after the last separator is not complete yet
  const events = blocks
    .filter((block) => block.startsWith(DATA_PREFIX))
    .map((block) => JSON.parse(block.slice(DATA_PREFIX.length)) as ChatEvent)
  return { events, rest }
}

/** Whether an event ends the stream: the contract's last event is one "done" or "error". */
export function isFinalEvent(event: ChatEvent): boolean {
  return event.type === 'done' || event.type === 'error'
}

/**
 * Send one message and call onEvent for each event the answer streams.
 *
 * Uses fetch rather than EventSource, which can only send GET requests (ADR 005).
 * Throws if the request fails or the stream ends without a final event.
 */
export async function streamChat(
  sessionId: string,
  message: string,
  choice: ModelChoice | null, // null: the backend's default models
  onEvent: (event: ChatEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message, ...choice }),
    signal,
  })
  if (response.status === 422) {
    // e.g. a model the backend no longer lists; its detail says which
    const body = (await response.json()) as { detail?: unknown }
    throw new Error(typeof body.detail === 'string' ? body.detail : 'The chat server rejected the message.')
  }
  if (!response.ok || !response.body) {
    throw new Error(`The chat server answered ${response.status}. Is the backend running?`)
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  let isFinished = false
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    const parsed = parseSse(buffer + value)
    buffer = parsed.rest
    for (const event of parsed.events) {
      onEvent(event)
      isFinished ||= isFinalEvent(event)
    }
  }
  if (!isFinished) throw new Error(STREAM_CUT_MESSAGE)
}
