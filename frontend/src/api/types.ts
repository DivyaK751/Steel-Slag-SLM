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

export type ChatEventType = 'status' | 'thinking' | 'token' | 'citations' | 'done' | 'error'

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

// GET /models: what the Reader and Writer pickers offer
export interface ModelInfo {
  id: string // the gateway's name, sent back in the chat request
  label: string
  supports_thinking: boolean
}

export interface ModelOptions {
  models: ModelInfo[]
  reader_default: string
  answer_default: string
}

// The model fields of POST /chat; left out, the backend uses its config defaults
export interface ModelChoice {
  reader_model: string
  answer_model: string
  thinking: boolean
}
