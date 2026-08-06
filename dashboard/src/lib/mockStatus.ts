/**
 * Whether this build is currently serving MSW mock data instead of the
 * real engine — the exact same condition `main.tsx` uses to decide whether
 * to start the mock worker. Kept in one place so a banner component and
 * `main.tsx` can never disagree.
 *
 * Found live on 2026-07-30: plain `yarn dev` (mocks on by default) showed
 * fabricated Learning/Performance numbers (Sharpe 1.2, DSR 0.71, 72%
 * "accuracy") with no visual indicator at all — indistinguishable from the
 * real engine's honest zero-state. `import.meta.env.DEV` is statically
 * `false` in a production build, so this is dead-code-eliminated there.
 */
export function isUsingMockData(): boolean {
  return import.meta.env.DEV && import.meta.env.VITE_USE_MOCKS !== 'false'
}
