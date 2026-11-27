import type { ReactNode } from 'react'

/** A 24×24 outline icon; decorative, so hidden from screen readers. */
function Icon({ children, size = 18 }: { children: ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

export const PlusIcon = () => <Icon><path d="M12 5v14M5 12h14" /></Icon>
export const ChatIcon = () => <Icon><path d="M4 5h16v11H9l-5 4z" /></Icon>
export const SearchIcon = () => <Icon><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></Icon>
export const UploadIcon = () => <Icon><path d="M12 15V4M7 9l5-5 5 5M4 15v4h16v-4" /></Icon>
export const ChevronDownIcon = () => <Icon size={16}><path d="m6 9 6 6 6-6" /></Icon>
export const ArrowUpIcon = () => <Icon><path d="M12 19V5M6 11l6-6 6 6" /></Icon>
export const PaperclipIcon = () => (
  <Icon><path d="m20 11-8.5 8.5a5 5 0 0 1-7-7L13 4a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L14 7" /></Icon>
)
export const FileIcon = () => <Icon><path d="M14 3H6v18h12V7zM14 3v4h4M9 12h6M9 16h6" /></Icon>
export const DatabaseIcon = () => (
  <Icon>
    <ellipse cx="12" cy="5" rx="8" ry="3" />
    <path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" />
  </Icon>
)
export const FlaskIcon = () => (
  <Icon><path d="M9 3h6M10 3v6l-5.5 9.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.5-2.5L14 9V3M7.5 15h9" /></Icon>
)
export const BrainIcon = () => (
  <Icon>
    <path d="M12 5a3 3 0 0 0-5.6-1.5A3 3 0 0 0 4 8a3 3 0 0 0 0 5 3 3 0 0 0 3 4 3 3 0 0 0 5 1z" />
    <path d="M12 5a3 3 0 0 1 5.6-1.5A3 3 0 0 1 20 8a3 3 0 0 1 0 5 3 3 0 0 1-3 4 3 3 0 0 1-5 1zM12 5v13" />
  </Icon>
)
export const LogoIcon = () => (
  <Icon size={22}>
    <path d="M3 19 9 9l4 6 3-4 5 8z" fill="currentColor" stroke="none" />
    <circle cx="17" cy="6" r="2" fill="#5fb98a" stroke="none" />
  </Icon>
)
