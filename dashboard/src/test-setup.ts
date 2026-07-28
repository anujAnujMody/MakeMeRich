import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { server } from './mocks/server'
import { resetMockState } from './mocks/handlers'

// jsdom doesn't implement these — Radix's Select/Popover use them for pointer
// capture and scroll-into-view on open. Without a no-op, any test that opens
// one throws "hasPointerCapture is not a function".
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {}
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {}
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}

// Node's built-in experimental localStorage global shadows jsdom's own
// implementation and stays undefined without --localstorage-file, so any
// zustand `persist`-backed store throws on its first write in tests. Minimal
// in-memory polyfill — good enough for tests, never touches a real disk file.
if (typeof globalThis.localStorage === 'undefined') {
  class MemoryStorage implements Storage {
    private store = new Map<string, string>()
    get length() { return this.store.size }
    clear() { this.store.clear() }
    getItem(key: string) { return this.store.has(key) ? this.store.get(key)! : null }
    key(index: number) { return Array.from(this.store.keys())[index] ?? null }
    removeItem(key: string) { this.store.delete(key) }
    setItem(key: string, value: string) { this.store.set(key, String(value)) }
  }
  Object.defineProperty(globalThis, 'localStorage', { value: new MemoryStorage(), writable: true, configurable: true })
}

// 'error' rather than 'warn' — an un-mocked endpoint should fail the test
// loudly, not scroll past silently. This is the main defense against contract
// drift once real backend endpoints start replacing mocked ones piecemeal.
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => { cleanup(); server.resetHandlers(); resetMockState() })
afterAll(() => server.close())
