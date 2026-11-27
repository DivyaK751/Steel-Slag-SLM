/** The step the assistant is on, e.g. "Searching papers…", shown before the answer starts. */
export function StatusLine({ text }: { text: string }) {
  return (
    <p className="status-line" role="status">
      {text}
    </p>
  )
}
