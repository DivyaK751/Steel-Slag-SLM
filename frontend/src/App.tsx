import { useState } from 'react'

import { Composer } from './components/Composer'
import { MessageList } from './components/MessageList'
import { ProfilePanel } from './components/ProfilePanel'
import { useChat } from './hooks/useChat'
import { useProfile } from './hooks/useProfile'

export default function App() {
  const { sessionId, messages, status, isStreaming, send, newChat } = useChat()
  const { profile, syncError, save } = useProfile(sessionId)
  const [isProfileOpen, setIsProfileOpen] = useState(false)

  return (
    <div className="app">
      <header className="app-header">
        <h1>Steel slag assistant</h1>
        <div className="header-actions">
          <button type="button" aria-expanded={isProfileOpen} onClick={() => setIsProfileOpen(!isProfileOpen)}>
            {profile ? `Your slag: ${profile.steel_slag_type}` : 'Add your slag'}
          </button>
          <button type="button" onClick={newChat}>
            New chat
          </button>
        </div>
      </header>
      {syncError && <p className="form-error banner">{syncError}</p>}
      {isProfileOpen && <ProfilePanel profile={profile} onSave={save} />}
      <MessageList messages={messages} status={status} />
      <Composer isDisabled={isStreaming} onSend={send} />
    </div>
  )
}
