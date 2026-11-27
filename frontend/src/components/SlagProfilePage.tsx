import { useEffect, useState, type FormEvent } from 'react'

import { fetchProfileOptions } from '../api/profile'
import type { ProfileOptions, SlagProfile } from '../api/types'
import { COMMON_OXIDES, EMPTY_FORM, toForm, toProfile, type ProfileForm } from '../profile/profileForm'

interface SlagProfilePageProps {
  profile: SlagProfile | null
  onSave: (profile: SlagProfile) => Promise<void>
}

/** The page for the user's slag: type, oxides (wt%), mineral phases and cooling. */
export function SlagProfilePage({ profile, onSave }: SlagProfilePageProps) {
  const [options, setOptions] = useState<ProfileOptions | null>(null)
  const [form, setForm] = useState<ProfileForm>(profile ? toForm(profile) : EMPTY_FORM)
  const [message, setMessage] = useState<{ text: string; isError: boolean } | null>(null)

  useEffect(() => {
    fetchProfileOptions()
      .then(setOptions)
      .catch((error: Error) => setMessage({ text: error.message, isError: true }))
  }, [])

  const header = (
    <header className="page-header">
      <div>
        <h1>Your slag</h1>
        <p>Answers compare your slag with similar ones in the papers and lab data.</p>
      </div>
    </header>
  )

  if (!options) {
    return (
      <main className="page">
        {header}
        <p className={message ? 'form-error' : 'form-note'}>{message?.text ?? 'Loading…'}</p>
      </main>
    )
  }

  const labels = Object.fromEntries(options.oxides.map((oxide) => [oxide.column, oxide.label]))
  const common = options.oxides.filter((oxide) => COMMON_OXIDES.includes(oxide.column))
  const more = options.oxides.filter((oxide) => !COMMON_OXIDES.includes(oxide.column))

  function setOxide(column: string, typed: string) {
    setForm((current) => ({ ...current, oxides: { ...current.oxides, [column]: typed } }))
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    const result = toProfile(form, labels)
    if (typeof result === 'string') return setMessage({ text: result, isError: true })
    try {
      await onSave(result)
      setMessage({ text: 'Saved. Answers will use this slag.', isError: false })
    } catch (error) {
      setMessage({ text: error instanceof Error ? error.message : String(error), isError: true })
    }
  }

  const oxideInput = (oxide: { column: string; label: string }) => (
    <label key={oxide.column}>
      {oxide.label}
      <input
        inputMode="decimal"
        placeholder="wt%"
        value={form.oxides[oxide.column] ?? ''}
        onChange={(event) => setOxide(oxide.column, event.target.value)}
      />
    </label>
  )

  return (
    <main className="page">
      {header}
      <form className="profile-card" onSubmit={submit}>
        <label>
          Slag type
          <select value={form.slagType} onChange={(event) => setForm({ ...form, slagType: event.target.value })}>
            <option value="">Choose…</option>
            {options.slag_types.map((type) => (
              <option key={type}>{type}</option>
            ))}
          </select>
        </label>
        <fieldset>
          <legend>Oxides (wt%)</legend>
          <div className="oxide-grid">{common.map(oxideInput)}</div>
        </fieldset>
        <details>
          <summary>More oxides</summary>
          <div className="oxide-grid">{more.map(oxideInput)}</div>
        </details>
        <label>
          Mineral phases
          <input
            placeholder="e.g. C2S, RO phase"
            value={form.phases}
            onChange={(event) => setForm({ ...form, phases: event.target.value })}
          />
        </label>
        <label>
          Cooling
          <input
            placeholder="e.g. air-cooled"
            value={form.cooling}
            onChange={(event) => setForm({ ...form, cooling: event.target.value })}
          />
        </label>
        <div className="profile-actions">
          <button type="submit" className="button-primary">
            Save profile
          </button>
          {message && <span className={message.isError ? 'form-error' : 'form-note'}>{message.text}</span>}
        </div>
      </form>
    </main>
  )
}
