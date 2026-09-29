import createClient, { type Middleware } from 'openapi-fetch'
import type { components, paths } from './schema'

export type Schemas = components['schemas']

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function cookie(name: string): string | undefined {
  return document.cookie
    .split('; ')
    .find((c) => c.startsWith(`${name}=`))
    ?.split('=')[1]
}

// Django checks CSRF on every state-changing request made with the session.
const csrf: Middleware = {
  onRequest({ request }) {
    if (request.method !== 'GET') {
      const token = cookie('csrftoken')
      if (token) request.headers.set('X-CSRFToken', decodeURIComponent(token))
    }
    return request
  },
}

export const client = createClient<paths>({ baseUrl: '', credentials: 'same-origin' })
client.use(csrf)

type Result<T> = { data?: T; error?: unknown; response: Response }

function detail(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'detail' in error) {
    const d = (error as { detail: unknown }).detail
    if (typeof d === 'string') return d
    if (Array.isArray(d)) return d.map((e) => (e as { msg?: string }).msg ?? String(e)).join('; ')
  }
  return fallback
}

/** Unwraps an openapi-fetch result, throwing an ApiError with the server's message. */
export async function unwrap<T>(promise: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await promise
  if (!response.ok) throw new ApiError(response.status, detail(error, response.statusText || 'Request failed'))
  return data as T
}
