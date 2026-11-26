import { describe, expect, it } from 'vitest'

import { EMPTY_FORM, toForm, toProfile } from './profileForm'

const LABELS = { cao_pct: 'CaO', sio2_pct: 'SiO2' }

describe('toProfile', () => {
  it('parses numbers and drops empty fields', () => {
    const form = { slagType: 'EAF', oxides: { cao_pct: '38.5', sio2_pct: ' ' }, phases: 'C2S, , RO phase', cooling: ' ' }

    expect(toProfile(form, LABELS)).toEqual({
      steel_slag_type: 'EAF',
      oxides: { cao_pct: 38.5 },
      mineral_phases: ['C2S', 'RO phase'],
      cooling: null,
    })
  })

  it('asks for the slag type when it is missing', () => {
    expect(toProfile(EMPTY_FORM, LABELS)).toBe('Choose the slag type.')
  })

  it.each(['abc', '-1', '101'])('rejects the wt%% %s by its oxide name', (typed) => {
    const form = { ...EMPTY_FORM, slagType: 'BOF', oxides: { cao_pct: typed } }

    expect(toProfile(form, LABELS)).toBe('CaO must be a number from 0 to 100.')
  })
})

describe('toForm', () => {
  it('fills the form so that saving it again gives the same profile', () => {
    const profile = { steel_slag_type: 'BOF', oxides: { cao_pct: 45 }, mineral_phases: ['C2S'], cooling: 'air-cooled' }

    expect(toProfile(toForm(profile), LABELS)).toEqual(profile)
  })
})
