const API_BASE = import.meta.env.VITE_OPENALGO_URL ?? 'http://localhost:5000'

export const api = {
  baseUrl: API_BASE,

  async get<T>(path: string): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`)
    if (!res.ok) throw new Error(`GET ${path}: ${res.status}`)
    return res.json()
  },

  async post<T>(path: string, body: unknown): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error(`POST ${path}: ${res.status}`)
    return res.json()
  },
}
