import { useEffect, useRef, useState } from 'react'

import { saveProfile } from '../api/profile'
import type { SlagProfile } from '../api/types'

const STORAGE_KEY = 'steel-slag-profile'

function loadStoredProfile(): SlagProfile | null {
  const stored = localStorage.getItem(STORAGE_KEY)
  try {
    return stored ? (JSON.parse(stored) as SlagProfile) : null
  } catch {
    return null // unreadable (edited by hand?): start without a profile
  }
}

/**
 * The user's slag profile. It belongs to the user rather than one chat, so it is kept in the
 * browser and sent again to every new chat session.
 */
export function useProfile(sessionId: string) {
  const [profile, setProfile] = useState(loadStoredProfile)
  const [syncError, setSyncError] = useState<string | null>(null)
  const latestProfile = useRef(profile)

  useEffect(() => {
    // Runs for each new session only; save() below sends a changed profile itself
    if (!latestProfile.current) return
    saveProfile(sessionId, latestProfile.current)
      .then(() => setSyncError(null))
      .catch((error: Error) => setSyncError(`Your saved profile was not sent: ${error.message}`))
  }, [sessionId])

  /** Send the profile to the current session, then keep it; throws if the server rejects it. */
  async function save(newProfile: SlagProfile) {
    await saveProfile(sessionId, newProfile)
    localStorage.setItem(STORAGE_KEY, JSON.stringify(newProfile))
    latestProfile.current = newProfile
    setProfile(newProfile)
    setSyncError(null)
  }

  return { profile, syncError, save }
}
