import { describe, expect, it } from 'vitest'

import type { ChatEvent } from '../api/types'
import { applyEvent, conversationFrom, type ChatMessage } from './useChat'

const EMPTY_ANSWER: ChatMessage = {
  id: 'a1',
  role: 'assistant',
  text: '',
  thinking: '',
  citations: [],
  isError: false,
  isDone: false,
}

function apply(events: Partial<ChatEvent>[]): ChatMessage {
  return events.reduce<ChatMessage>(
    (message, event) => applyEvent(message, { type: 'token', text: '', citations: [], ...event }),
    EMPTY_ANSWER,
  )
}

describe('applyEvent', () => {
  it('joins tokens into the answer and ignores status steps', () => {
    const message = apply([
      { type: 'status', text: 'Searching papers…' },
      { type: 'token', text: 'Steel ' },
      { type: 'token', text: 'slag.' },
      { type: 'done' },
    ])

    expect(message.text).toBe('Steel slag.')
    expect(message.isError).toBe(false)
    expect(message.isDone).toBe(true)
  })

  it('keeps the reasoning apart from the answer', () => {
    const message = apply([
      { type: 'thinking', text: 'They ask about ' },
      { type: 'thinking', text: 'BOF.' },
      { type: 'token', text: 'BOF slag swells.' },
    ])

    expect(message.thinking).toBe('They ask about BOF.')
    expect(message.text).toBe('BOF slag swells.')
  })

  it('stores the citations event', () => {
    const citation = { label: 'P1', paper_id: 'W1', title: null, authors: [], year: null, doi: null, pages: [] }

    expect(apply([{ type: 'citations', citations: [citation] }]).citations).toEqual([citation])
  })

  it('keeps the partial answer and adds the error after it', () => {
    const message = apply([{ type: 'token', text: 'Steel ' }, { type: 'error', text: 'Server failed.' }])

    expect(message.text).toBe('Steel \n\nServer failed.')
    expect(message.isError).toBe(true)
    expect(message.isDone).toBe(true)
  })
})

describe('conversationFrom', () => {
  it('builds a user question and a finished answer with its citations', () => {
    const citation = { label: 'P1', paper_id: 'W1', title: null, authors: [], year: null, doi: null, pages: [] }
    const [question, answer] = conversationFrom('Why?', 'Because [P1].', [citation])

    expect(question).toMatchObject({ role: 'user', text: 'Why?', isDone: true })
    expect(answer).toMatchObject({ role: 'assistant', text: 'Because [P1].', citations: [citation], isDone: true })
  })
})
