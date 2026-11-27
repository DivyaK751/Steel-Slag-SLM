import type { ModelOptions } from './types'

/** Fetch the models the Reader and Writer pickers offer, and each job's default. */
export async function fetchModels(): Promise<ModelOptions> {
  const response = await fetch('/models')
  if (!response.ok) throw new Error(`Could not load the models (${response.status}).`)
  return (await response.json()) as ModelOptions
}
