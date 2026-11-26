import { useEffect, useRef } from 'react'

import type { ChatMessage } from '../hooks/useChat'
import { Message } from './Message'
import { StatusLine } from './StatusLine'

interface MessageListProps {
  messages: ChatMessage[]
  status: string | null
}

/** The conversation so far, kept scrolled to the newest text. */
export function MessageList({ messages, status }: MessageListProps) {
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' })
  }, [messages, status])

  if (messages.length === 0) {
    return (
      <div className="message-list message-list--empty">
        <p>Ask about steel slag: its chemistry, processing, or use in concrete.</p>
      </div>
    )
  }
  return (
    <div className="message-list">
      {messages
        .filter((message) => message.text) // the answer appears once its first token arrives
        .map((message) => (
          <Message key={message.id} message={message} />
        ))}
      {status && <StatusLine text={status} />}
      <div ref={end} />
    </div>
  )
}
