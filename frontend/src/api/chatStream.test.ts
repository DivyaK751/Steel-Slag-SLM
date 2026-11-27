import { afterEach, describe, expect, it, vi } from 'vitest'

import { isFinalEvent, parseSse, streamChat } from './chatStream'
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
    const types = ['status', 'thinking', 'token', 'citations', 'done', 'error'] as const
    const finals = types.filter((type) => isFinalEvent({ type, text: '', citations: [] }))

    expect(finals).toEqual(['done', 'error'])
  })
})

describe('streamChat', () => {
  afterEach(() => vi.unstubAllGlobals())

  function stubFetch(response: Response) {
    const fetchMock = vi.fn().mockResolvedValue(response)
    vi.stubGlobal('fetch', fetchMock)
    return fetchMock
  }

  it('sends the picked models with the message', async () => {
    const fetchMock = stubFetch(new Response(sse({ type: 'done' })))
    const choice = { reader_model: 'gemini-3.8-flash', answer_model: 'qwen3-8b', thinking: true }

    await streamChat('s1', 'Why?', choice, () => {}, new AbortController().signal)

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string)
    expect(body).toEqual({ session_id: 's1', message: 'Why?', ...choice })
  })

  it('sends no model fields before the models load', async () => {
    const fetchMock = stubFetch(new Response(sse({ type: 'done' })))

    await streamChat('s1', 'Why?', null, () => {}, new AbortController().signal)

    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({ session_id: 's1', message: 'Why?' })
  })

  it("shows the server's reason when it rejects a model", async () => {
    stubFetch(new Response(JSON.stringify({ detail: "Unknown model 'gpt-x'" }), { status: 422 }))
    const choice = { reader_model: 'gpt-x', answer_model: 'gpt-x', thinking: false }

    await expect(streamChat('s1', 'Why?', choice, () => {}, new AbortController().signal)).rejects.toThrow(
      "Unknown model 'gpt-x'",
    )
  })
})
