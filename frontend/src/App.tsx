import { useState } from 'react'

import { Composer } from './components/Composer'
import { KnowledgeSources } from './components/KnowledgeSources'
import { MessageList } from './components/MessageList'
import { Sidebar, type ChatSummary } from './components/Sidebar'
import { SlagProfilePage } from './components/SlagProfilePage'
import { WelcomeScreen } from './components/WelcomeScreen'
import { conversationFrom, useChat } from './hooks/useChat'
import { useModels } from './hooks/useModels'
import { useProfile } from './hooks/useProfile'
import { BIGQUERY_TABLES, RESEARCH_PAPERS, SAMPLE_CHATS } from './placeholder/sampleData'

const LIVE_CHAT_ID = 'live' // the chat being typed in now; the rest of the list is sample history
const NEW_CHAT_TITLE = 'New research chat'
const INDEXED_SUMMARY = `${RESEARCH_PAPERS.length} papers · ${BIGQUERY_TABLES.length} tables indexed`

export default function App() {
  const { sessionId, messages, status, isStreaming, send, newChat, openChat } = useChat()
  const { profile, syncError, save } = useProfile(sessionId)
  const models = useModels()
  const [page, setPage] = useState<'chat' | 'sources' | 'profile'>('chat')
  const [activeChatId, setActiveChatId] = useState(LIVE_CHAT_ID)
  const [liveTitle, setLiveTitle] = useState<string | null>(null)

  const activeSample = SAMPLE_CHATS.find((chat) => chat.id === activeChatId)
  const chatTitle = activeSample?.title ?? liveTitle ?? NEW_CHAT_TITLE
  const chats: ChatSummary[] = [{ id: LIVE_CHAT_ID, title: liveTitle ?? NEW_CHAT_TITLE, when: 'Just now' }, ...SAMPLE_CHATS]

  function startNewChat() {
    newChat()
    setLiveTitle(null)
    setActiveChatId(LIVE_CHAT_ID)
    setPage('chat')
  }

  function openSidebarChat(id: string) {
    setPage('chat')
    if (id === activeChatId) return
    const sample = SAMPLE_CHATS.find((chat) => chat.id === id)
    if (!sample) return startNewChat()
    openChat(conversationFrom(sample.question, sample.answer, sample.citations))
    setActiveChatId(id)
  }

  function sendMessage(text: string) {
    if (activeChatId === LIVE_CHAT_ID && !liveTitle) setLiveTitle(text)
    void send(text, models.choice, models.writerLabel)
  }

  return (
    <div className="app">
      <Sidebar
        chats={chats}
        activeChatId={page === 'chat' ? activeChatId : null}
        page={page}
        onNewChat={startNewChat}
        onOpenChat={openSidebarChat}
        onOpenSources={() => setPage('sources')}
        onOpenProfile={() => setPage('profile')}
      />
      {page === 'sources' && <KnowledgeSources />}
      {page === 'profile' && <SlagProfilePage profile={profile} onSave={save} />}
      {page === 'chat' && (
        <main className="chat">
          <header className="chat-header">
            <h1>{chatTitle}</h1>
            <span className="indexed-pill">{INDEXED_SUMMARY}</span>
          </header>
          {syncError && <p className="form-error banner">{syncError}</p>}
          {models.loadError && <p className="form-error banner">{models.loadError}</p>}
          <div className="chat-body">
            {messages.length === 0 ? (
              <WelcomeScreen onPick={sendMessage} />
            ) : (
              <MessageList messages={messages} status={status} />
            )}
            <Composer
              isDisabled={isStreaming}
              models={models.models}
              readerId={models.readerId}
              writerId={models.writerId}
              isThinking={models.isThinking}
              canThink={models.canThink}
              onReaderChange={models.setReaderId}
              onWriterChange={models.setWriterId}
              onThinkingChange={models.setIsThinking}
              onSend={sendMessage}
            />
            <p className="disclaimer">Answers cite indexed sources. Check figures before publishing.</p>
          </div>
        </main>
      )}
    </div>
  )
}
