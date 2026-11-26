// Mirrors Citation and ChatEvent in src/steel_slag_slm/schemas.py (contract: docs/adr/005)

export interface Citation {
  label: string // as written in the answer, without brackets, e.g. "P3"
  paper_id: string
  title: string | null
  authors: string[]
  year: number | null
  doi: string | null
  pages: number[]
}

export type ChatEventType = 'status' | 'token' | 'citations' | 'done' | 'error'

export interface ChatEvent {
  type: ChatEventType
  text: string
  citations: Citation[]
}
