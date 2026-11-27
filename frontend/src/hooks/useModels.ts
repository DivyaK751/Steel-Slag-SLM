import { useEffect, useState } from 'react'

import { fetchModels } from '../api/models'
import type { ModelChoice, ModelInfo } from '../api/types'

/** The Reader and Writer pickers: the gateway's models, the picks, and the Thinking switch. */
export function useModels() {
  const [models, setModels] = useState<ModelInfo[]>([])
  const [readerId, setReaderId] = useState('')
  const [writerId, setWriterId] = useState('')
  const [isThinking, setIsThinking] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    fetchModels()
      .then((options) => {
        setModels(options.models)
        setReaderId(options.reader_default)
        setWriterId(options.answer_default)
      })
      .catch((error: Error) => setLoadError(error.message))
  }, [])

  const reader = models.find((model) => model.id === readerId)
  const writer = models.find((model) => model.id === writerId)
  // Only some models (Qwen) can switch thinking; Gemini always reasons internally
  const canThink = Boolean(reader?.supports_thinking || writer?.supports_thinking)
  // Until the models load, send no choice, so the backend uses its defaults
  const choice: ModelChoice | null =
    reader && writer ? { reader_model: reader.id, answer_model: writer.id, thinking: isThinking && canThink } : null

  return {
    models,
    readerId,
    writerId,
    writerLabel: writer?.label ?? 'Assistant',
    isThinking: isThinking && canThink,
    canThink,
    choice,
    loadError,
    setReaderId,
    setWriterId,
    setIsThinking,
  }
}
