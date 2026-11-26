import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import type { ChatMessage } from '../hooks/useChat'

// remark-gfm renders the small tables the answer prompt asks for when comparing values
const MARKDOWN_PLUGINS = [remarkGfm]

/** One chat message: the user's text as typed, the assistant's answer as markdown. */
export function Message({ message }: { message: ChatMessage }) {
  const classes = ['message', `message--${message.role}`, message.isError && 'message--error']
  return (
    <div className={classes.filter(Boolean).join(' ')}>
      {message.role === 'user' ? (
        <p>{message.text}</p>
      ) : (
        <Markdown remarkPlugins={MARKDOWN_PLUGINS}>{message.text}</Markdown>
      )}
    </div>
  )
}
