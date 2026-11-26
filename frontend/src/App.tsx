import { Composer } from './components/Composer'
import { MessageList } from './components/MessageList'
import { useChat } from './hooks/useChat'

export default function App() {
  const { messages, status, isStreaming, send, newChat } = useChat()

  return (
    <div className="app">
      <header className="app-header">
        <h1>Steel slag assistant</h1>
        <button type="button" onClick={newChat}>
          New chat
        </button>
      </header>
      <MessageList messages={messages} status={status} />
      <Composer isDisabled={isStreaming} onSend={send} />
    </div>
  )
}
