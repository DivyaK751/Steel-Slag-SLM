import { useRef, useState } from 'react'

import { streamChat } from '../api/chatStream'
import type { ChatEvent, Citation, ModelChoice } from '../api/types'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  thinking: string // the writer's reasoning, when thinking is on; never cited
  modelLabel?: string // the writer that answered, kept so the label survives a new pick
  citations: Citation[]
  isError: boolean
  isDone: boolean // the stream ended ("done" or "error"), so its citations are final
}

function newMessage(role: ChatMessage['role'], text = ''): ChatMessage {
  return { id: crypto.randomUUID(), role, text, thinking: '', citations: [], isError: false, isDone: role === 'user' }
}

/** A finished question-and-answer pair, e.g. a saved chat being reopened. */
export function conversationFrom(question: string, answer: string, citations: Citation[]): ChatMessage[] {
  return [newMessage('user', question), { ...newMessage('assistant', answer), citations, isDone: true }]
}

/** Return the assistant message with one streamed event applied; status events don't change it. */
export function applyEvent(message: ChatMessage, event: ChatEvent): ChatMessage {
  switch (event.type) {
    case 'thinking':
      return { ...message, thinking: message.thinking + event.text }
    case 'token':
      return { ...message, text: message.text + event.text }
    case 'citations':
      return { ...message, citations: event.citations }
    case 'done':
      return { ...message, isDone: true }
    case 'error': {
      // Keep any answer text already shown, and add the error after it
      const text = [message.text, event.text].filter(Boolean).join('\n\n')
      return { ...message, text, isError: true, isDone: true }
    }
    default:
      return message
  }
}

/** One chat with the backend: its messages, the current status step, and send/new chat. */
export function useChat() {
  const [sessionId, setSessionId] = useState(() => crypto.randomUUID())
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [status, setStatus] = useState<string | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const controller = useRef<AbortController | null>(null)

  function updateMessage(id: string, update: (message: ChatMessage) => ChatMessage) {
    setMessages((current) => current.map((message) => (message.id === id ? update(message) : message)))
  }

  function handleEvent(answerId: string, event: ChatEvent) {
    // The status line shows until the first piece of the answer arrives
    setStatus(event.type === 'status' ? event.text : null)
    updateMessage(answerId, (message) => applyEvent(message, event))
  }

  /** Send a message with the picked models (null: the backend's defaults). */
  async function send(text: string, choice: ModelChoice | null, modelLabel: string) {
    const answer = { ...newMessage('assistant'), modelLabel }
    setMessages((current) => [...current, newMessage('user', text), answer])
    setIsStreaming(true)
    const thisRequest = new AbortController()
    controller.current = thisRequest
    try {
      await streamChat(sessionId, text, choice, (event) => handleEvent(answer.id, event), thisRequest.signal)
    } catch (error) {
      if (thisRequest.signal.aborted) return // the user started a new chat
      const text = error instanceof Error ? error.message : String(error)
      updateMessage(answer.id, (message) => applyEvent(message, { type: 'error', text, citations: [] }))
    } finally {
      if (controller.current === thisRequest) {
        setStatus(null)
        setIsStreaming(false)
      }
    }
  }

  /** Stop any answer in progress and start a fresh session showing these messages. */
  function openChat(initialMessages: ChatMessage[]) {
    controller.current?.abort()
    controller.current = null
    setSessionId(crypto.randomUUID())
    setMessages(initialMessages)
    setStatus(null)
    setIsStreaming(false)
  }

  const newChat = () => openChat([])

  return { sessionId, messages, status, isStreaming, send, newChat, openChat }
}
