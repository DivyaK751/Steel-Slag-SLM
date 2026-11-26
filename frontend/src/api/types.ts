// Mirrors Citation and ChatEvent in src/steel_slag_slm/schemas.py and SlagProfile in
// rag/similar_slags.py (contract: docs/adr/005)

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

export interface SlagProfile {
  steel_slag_type: string
  oxides: Record<string, number> // wt% by catalogue column, e.g. { cao_pct: 38 }
  mineral_phases: string[]
  cooling: string | null
}

// GET /profile/options: the lists the backend validates a profile against
export interface ProfileOptions {
  slag_types: string[]
  oxides: { column: string; label: string }[]
}
