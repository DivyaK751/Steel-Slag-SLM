import { ChatIcon, DatabaseIcon, FlaskIcon, LogoIcon, PlusIcon } from './Icons'

export interface ChatSummary {
  id: string
  title: string
  when: string
}

interface SidebarProps {
  chats: ChatSummary[]
  activeChatId: string | null // null while another page is open
  page: 'chat' | 'sources' | 'profile'
  onNewChat: () => void
  onOpenChat: (id: string) => void
  onOpenSources: () => void
  onOpenProfile: () => void
}

/** The left column: brand, new chat, knowledge sources and the chat history. */
export function Sidebar(props: SidebarProps) {
  const { chats, activeChatId, page, onNewChat, onOpenChat, onOpenSources, onOpenProfile } = props
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-logo">
          <LogoIcon />
        </span>
        <div>
          <strong>SlagMind</strong>
          <span>Carbon-negative research</span>
        </div>
      </div>
      <button type="button" className="sidebar-new" onClick={onNewChat}>
        <PlusIcon /> New chat
      </button>
      <button
        type="button"
        className={page === 'sources' ? 'sidebar-item is-active' : 'sidebar-item'}
        onClick={onOpenSources}
      >
        <DatabaseIcon /> Knowledge sources
      </button>
      <button
        type="button"
        className={page === 'profile' ? 'sidebar-item is-active' : 'sidebar-item'}
        onClick={onOpenProfile}
      >
        <FlaskIcon /> Slag profiles
      </button>
      <h2 className="sidebar-heading">Chats</h2>
      <nav className="chat-list">
        {chats.map((chat) => (
          <button
            key={chat.id}
            type="button"
            className={chat.id === activeChatId ? 'chat-link is-active' : 'chat-link'}
            onClick={() => onOpenChat(chat.id)}
          >
            <ChatIcon />
            <span>
              <span className="chat-link-title">{chat.title}</span>
              <span className="chat-link-when">{chat.when}</span>
            </span>
          </button>
        ))}
      </nav>
    </aside>
  )
}
