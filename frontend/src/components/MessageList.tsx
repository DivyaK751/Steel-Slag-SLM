import { useEffect, useRef } from 'react'

import type { ChatMessage } from '../hooks/useChat'
import { Message } from './Message'
import { StatusLine } from './StatusLine'

interface MessageListProps {
  messages: ChatMessage[]
  status: string | null
  modelLabel: string
}

/** The conversation so far, kept scrolled to the newest text. */
export function MessageList({ messages, status, modelLabel }: MessageListProps) {
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' })
  }, [messages, status])

  return (
    <div className="message-list">
      {messages
        .filter((message) => message.text) // the answer appears once its first token arrives
        .map((message) => (
          <Message key={message.id} message={message} modelLabel={modelLabel} />
        ))}
      {status && <StatusLine text={status} />}
      <div ref={end} />
    </div>
  )
}
