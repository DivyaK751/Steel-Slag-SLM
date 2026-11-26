import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { CITATION_HREF_PREFIX, linkCitations } from '../citations/linkCitations'
import type { ChatMessage } from '../hooks/useChat'
import { CitationChip } from './CitationChip'

// remark-gfm renders the small tables the answer prompt asks for when comparing values
const MARKDOWN_PLUGINS = [remarkGfm]

/** Markdown links become citation chips when linkCitations made them, else open in a new tab. */
function answerComponents(message: ChatMessage): Components {
  const citations = new Map(message.citations.map((citation) => [citation.label, citation]))
  return {
    a: ({ href, children }) => {
      if (href?.startsWith(CITATION_HREF_PREFIX)) {
        const label = href.slice(CITATION_HREF_PREFIX.length)
        return <CitationChip label={label} citation={citations.get(label)} isAnswerDone={message.isDone} />
      }
      return (
        <a href={href} target="_blank" rel="noreferrer">
          {children}
        </a>
      )
    },
  }
}

/** One chat message: the user's text as typed, the assistant's answer as markdown. */
export function Message({ message }: { message: ChatMessage }) {
  const classes = ['message', `message--${message.role}`, message.isError && 'message--error']
  return (
    <div className={classes.filter(Boolean).join(' ')}>
      {message.role === 'user' ? (
        <p>{message.text}</p>
      ) : (
        <Markdown remarkPlugins={MARKDOWN_PLUGINS} components={answerComponents(message)}>
          {linkCitations(message.text)}
        </Markdown>
      )}
    </div>
  )
}
