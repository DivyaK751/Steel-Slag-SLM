// A bracket holding only [S#]/[P#] labels, e.g. "[S1]" or "[S1, P2]", and not already a link.
// Labels match agents/answer_writer.py, which checks the same labels for made-up citations.
const CITATION_BRACKET = /\[\s*([SP]\d+(?:\s*[,;]\s*[SP]\d+)*)\s*\](?!\()/g
const LABEL = /[SP]\d+/g

export const CITATION_HREF_PREFIX = '#cite-'

/** Rewrite each cited label as a markdown link, "[P1](#cite-P1)", so it can render as a chip. */
export function linkCitations(markdown: string): string {
  return markdown.replace(CITATION_BRACKET, (_bracket, labels: string) =>
    (labels.match(LABEL) ?? []).map((label) => `[${label}](${CITATION_HREF_PREFIX}${label})`).join(''),
  )
}
