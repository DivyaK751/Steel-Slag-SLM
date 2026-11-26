import type { Citation } from '../api/types'

const MAX_AUTHORS = 3

function formatAuthors(authors: string[]): string {
  if (authors.length <= MAX_AUTHORS) return authors.join(', ')
  return `${authors.slice(0, MAX_AUTHORS).join(', ')} et al.`
}

interface CitationChipProps {
  label: string
  citation: Citation | undefined
  isAnswerDone: boolean // citations arrive at the end, so a missing one only counts after that
}

/** A cited [S#]/[P#] label; hovering or focusing it shows the paper it comes from. */
export function CitationChip({ label, citation, isAnswerDone }: CitationChipProps) {
  if (!citation) {
    const note = isAnswerDone ? 'This source is not in the evidence.' : undefined
    return (
      <span className={isAnswerDone ? 'citation citation--unknown' : 'citation'} title={note}>
        {label}
      </span>
    )
  }
  const pages = citation.pages.length ? `p. ${citation.pages.join(', ')}` : null
  return (
    <span className="citation" tabIndex={0}>
      {label}
      <span className="citation-card" role="tooltip">
        <strong>{citation.title ?? citation.paper_id}</strong>
        {citation.authors.length > 0 && <span>{formatAuthors(citation.authors)}</span>}
        <span>{[citation.year ?? 'n.d.', pages].filter(Boolean).join(' · ')}</span>
        {citation.doi && (
          <a href={`https://doi.org/${citation.doi}`} target="_blank" rel="noreferrer">
            doi:{citation.doi}
          </a>
        )}
      </span>
    </span>
  )
}
