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

  return (
    <div className="message-list">
      {messages
        // The answer appears once its first reasoning or answer text arrives
        .filter((message) => message.text || message.thinking.trim())
        .map((message) => (
          <Message key={message.id} message={message} />
        ))}
      {status && <StatusLine text={status} />}
      <div ref={end} />
    </div>
  )
}
