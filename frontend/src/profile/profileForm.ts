import type { SlagProfile } from '../api/types'

// Shown up front in the form; every other catalogue oxide sits under "More oxides"
export const COMMON_OXIDES = ['cao_pct', 'sio2_pct', 'al2o3_pct', 'feo_pct', 'fe2o3_pct', 'mgo_pct', 'mno_pct']

/** The profile form as typed: every field is text until it is checked. */
export interface ProfileForm {
  slagType: string
  oxides: Record<string, string> // catalogue column -> wt% as typed
  phases: string // comma-separated, e.g. "C2S, RO phase"
  cooling: string
}

export const EMPTY_FORM: ProfileForm = { slagType: '', oxides: {}, phases: '', cooling: '' }

/** Fill the form from a saved profile. */
export function toForm(profile: SlagProfile): ProfileForm {
  return {
    slagType: profile.steel_slag_type,
    oxides: Object.fromEntries(Object.entries(profile.oxides).map(([column, pct]) => [column, String(pct)])),
    phases: profile.mineral_phases.join(', '),
    cooling: profile.cooling ?? '',
  }
}

/**
 * Check the form and build the profile the API takes, dropping empty fields.
 *
 * Returns an error message instead when the slag type is missing or a wt% is not 0-100.
 */
export function toProfile(form: ProfileForm, oxideLabels: Record<string, string>): SlagProfile | string {
  if (!form.slagType) return 'Choose the slag type.'
  const oxides: Record<string, number> = {}
  for (const [column, typed] of Object.entries(form.oxides)) {
    if (typed.trim() === '') continue
    const pct = Number(typed)
    if (!Number.isFinite(pct) || pct < 0 || pct > 100) {
      return `${oxideLabels[column] ?? column} must be a number from 0 to 100.`
    }
    oxides[column] = pct
  }
  const phases = form.phases.split(',').map((phase) => phase.trim()).filter(Boolean)
  return { steel_slag_type: form.slagType, oxides, mineral_phases: phases, cooling: form.cooling.trim() || null }
}
