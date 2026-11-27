import type { Citation } from '../api/types'

/** "Huijgen 2005 · Title": first author's surname, year and title. */
function pillLabel(citation: Citation): string {
  const surname = citation.authors[0]?.split(' ').at(-1)
  const lead = [surname, citation.year].filter(Boolean).join(' ')
  const title = citation.title ?? citation.paper_id
  return lead ? `${lead} · ${title}` : title
}

/** The papers an answer cited, one pill each, below the answer. */
export function SourcePills({ citations }: { citations: Citation[] }) {
  // The same paper can be cited under several labels; show it once
  const papers = [...new Map(citations.map((citation) => [citation.paper_id, citation])).values()]
  if (papers.length === 0) return null
  return (
    <div className="source-pills">
      {papers.map((citation) =>
        citation.doi ? (
          <a key={citation.paper_id} className="source-pill" href={`https://doi.org/${citation.doi}`} target="_blank" rel="noreferrer">
            {pillLabel(citation)}
          </a>
        ) : (
          <span key={citation.paper_id} className="source-pill">
            {pillLabel(citation)}
          </span>
        ),
      )}
    </div>
  )
}
