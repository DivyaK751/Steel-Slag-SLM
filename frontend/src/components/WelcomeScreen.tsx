import { SUGGESTED_QUESTIONS } from '../placeholder/sampleData'

/** The empty chat: a greeting and suggested questions that send when clicked. */
export function WelcomeScreen({ onPick }: { onPick: (question: string) => void }) {
  return (
    <div className="welcome">
      <h2>What are we sequestering today?</h2>
      <p>Ask about steel slag chemistry, carbonation experiments or carbon accounting.</p>
      <div className="suggestions">
        {SUGGESTED_QUESTIONS.map((question) => (
          <button key={question} type="button" className="suggestion" onClick={() => onPick(question)}>
            {question}
          </button>
        ))}
      </div>
    </div>
  )
}
