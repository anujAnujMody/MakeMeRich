import { useSettingsStore } from '@/stores/settingsStore'

describe('settingsStore', () => {
  beforeEach(() => {
    useSettingsStore.setState({ openrouterKey: '', researchTime: '08:00' })
    localStorage.removeItem('algo-settings')
  })

  it('setOpenrouterKey updates state', () => {
    useSettingsStore.getState().setOpenrouterKey('sk-or-secret')
    expect(useSettingsStore.getState().openrouterKey).toBe('sk-or-secret')
  })

  it('never persists openrouterKey to localStorage, even after it is set', () => {
    useSettingsStore.getState().setOpenrouterKey('sk-or-secret')
    useSettingsStore.getState().setResearchTime('09:30')

    const persisted = JSON.parse(localStorage.getItem('algo-settings') ?? '{}')
    expect(persisted.state).not.toHaveProperty('openrouterKey')
    // Sanity check the mechanism actually ran — other fields ARE persisted.
    expect(persisted.state.researchTime).toBe('09:30')
  })
})
