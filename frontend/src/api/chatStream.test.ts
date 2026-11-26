import { describe, expect, it } from 'vitest'

import { isFinalEvent, parseSse } from './chatStream'
import type { ChatEvent } from './types'

function sse(event: Partial<ChatEvent>): string {
  return `data: ${JSON.stringify({ text: '', citations: [], ...event })}\n\n`
}

describe('parseSse', () => {
  it('reads several events from one chunk', () => {
    const { events, rest } = parseSse(sse({ type: 'status', text: 'Searching papers…' }) + sse({ type: 'done' }))

    expect(events.map((event) => event.type)).toEqual(['status', 'done'])
    expect(events[0].text).toBe('Searching papers…')
    expect(rest).toBe('')
  })

  it('keeps an event split across reads until it is complete', () => {
    const whole = sse({ type: 'token', text: 'Steel ' })
    const first = parseSse(whole.slice(0, 20))
    const second = parseSse(first.rest + whole.slice(20))

    expect(first.events).toEqual([])
    expect(second.events.map((event) => event.text)).toEqual(['Steel '])
    expect(second.rest).toBe('')
  })

  it('reads events separated by CRLF line endings', () => {
    const { events } = parseSse(sse({ type: 'done' }).replaceAll('\n', '\r\n'))

    expect(events.map((event) => event.type)).toEqual(['done'])
  })
})

describe('isFinalEvent', () => {
  it('treats only done and error as the end of the stream', () => {
    const types = ['status', 'token', 'citations', 'done', 'error'] as const
    const finals = types.filter((type) => isFinalEvent({ type, text: '', citations: [] }))

    expect(finals).toEqual(['done', 'error'])
  })
})
