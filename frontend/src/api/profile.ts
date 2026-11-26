import type { ProfileOptions, SlagProfile } from './types'

interface ValidationError {
  loc: (string | number)[]
  msg: string
}

/** Turn a FastAPI 422 body into one readable line, e.g. "oxides: unknown oxide columns ...". */
function describeValidationError(body: { detail?: ValidationError[] | string }): string {
  if (!Array.isArray(body.detail)) return body.detail ?? 'The profile was not accepted.'
  // loc starts with "body"; the rest names the field
  return body.detail.map((error) => `${error.loc.slice(1).join('.')}: ${error.msg}`).join('; ')
}

/** Fetch the slag types and oxides the profile form offers. */
export async function fetchProfileOptions(): Promise<ProfileOptions> {
  const response = await fetch('/profile/options')
  if (!response.ok) throw new Error(`Could not load the profile form (${response.status}).`)
  return (await response.json()) as ProfileOptions
}

/** Save the profile for a chat session; throws with the server's reason if it is rejected. */
export async function saveProfile(sessionId: string, profile: SlagProfile): Promise<void> {
  const response = await fetch(`/session/${sessionId}/profile`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profile),
  })
  if (response.status === 422) throw new Error(describeValidationError(await response.json()))
  if (!response.ok) throw new Error(`Could not save the profile (${response.status}).`)
}
