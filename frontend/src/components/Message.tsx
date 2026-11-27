import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { CITATION_HREF_PREFIX, linkCitations } from '../citations/linkCitations'
import type { ChatMessage } from '../hooks/useChat'
import { CitationChip } from './CitationChip'
import { SourcePills } from './SourcePills'

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

/** The writer's reasoning, open while it streams and folded away once the answer starts. */
function ThinkingBlock({ thinking, isAnswering }: { thinking: string; isAnswering: boolean }) {
  return (
    <details className="thinking-block" open={!isAnswering}>
      <summary>{isAnswering ? 'Thoughts' : 'Thinking…'}</summary>
      <p>{thinking}</p>
    </details>
  )
}

/** One chat message: the user's text as typed, the assistant's answer as markdown with its sources. */
export function Message({ message }: { message: ChatMessage }) {
  if (message.role === 'user') {
    return (
      <div className="message message--user">
        <p>{message.text}</p>
      </div>
    )
  }
  return (
    <div className={message.isError ? 'message message--assistant message--error' : 'message message--assistant'}>
      <span className="message-author">{message.modelLabel ?? 'Assistant'}</span>
      {message.thinking.trim() && (
        <ThinkingBlock thinking={message.thinking.trim()} isAnswering={message.text !== '' || message.isDone} />
      )}
      <Markdown remarkPlugins={MARKDOWN_PLUGINS} components={answerComponents(message)}>
        {linkCitations(message.text)}
      </Markdown>
      {message.isDone && <SourcePills citations={message.citations} />}
    </div>
  )
}
