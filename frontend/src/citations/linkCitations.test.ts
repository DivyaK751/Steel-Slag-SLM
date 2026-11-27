import { describe, expect, it } from 'vitest'

import { linkCitations } from './linkCitations'

describe('linkCitations', () => {
  it('links one label', () => {
    expect(linkCitations('42.5 MPa [S1].')).toBe('42.5 MPa [S1](#cite-S1).')
  })

  it('links labels side by side and inside one bracket', () => {
    expect(linkCitations('[S1][P12] and [S2, P3]')).toBe(
      '[S1](#cite-S1)[P12](#cite-P12) and [S2](#cite-S2)[P3](#cite-P3)',
    )
  })

  it('leaves other brackets and existing links alone', () => {
    const text = 'see [the table], [S], [P1a] and [P1](https://example.org)'

    expect(linkCitations(text)).toBe(text)
  })
})
