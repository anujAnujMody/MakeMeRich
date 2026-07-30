import { useShallow } from 'zustand/react/shallow'
import { useSettingsStore } from '@/stores/settingsStore'
import { useInstrumentSelections, useSaveInstrumentSelections } from '@/hooks/useAgentData'
import { PageHeader } from '@/components/PageHeader'
import { TradingModeCard } from '@/components/TradingModeCard'
import { AccountGuardrailsCard } from '@/components/AccountGuardrailsCard'
import { StrategyConfigEditor } from '@/components/StrategyConfigEditor'
import { AppearanceCard } from '@/components/AppearanceCard'
import { Toggle } from '@/components/Toggle'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Key, Brain, FlaskConical } from 'lucide-react'

const MODELS = [
  { id: 'anthropic/claude-sonnet-4.6', label: 'Claude Sonnet 4.6' },
  { id: 'openai/gpt-5-mini', label: 'GPT-5-mini' },
  { id: 'deepseek/deepseek-v4-flash', label: 'DeepSeek V4-Flash' },
  { id: 'openai/gpt-5', label: 'GPT-5' },
  { id: 'anthropic/claude-opus-4.7', label: 'Claude Opus 4.7' },
  { id: 'google/gemini-3-flash', label: 'Gemini 3 Flash' },
]

function ModelSelect({
  label,
  hint,
  value,
  onChange,
  className,
}: {
  label: string
  hint: string
  value: string
  onChange: (value: string) => void
  className?: string
}) {
  return (
    <div>
      <label className="text-xs text-muted-foreground">{label}</label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className={className ?? 'h-9 text-xs'} aria-label={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {MODELS.map((m) => (
            <SelectItem key={m.id} value={m.id} className="text-xs">
              {m.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p className="mt-0.5 text-[10px] text-muted-foreground">{hint}</p>
    </div>
  )
}

export function SettingsPage() {
  const store = useSettingsStore(
    useShallow((s) => ({
      autonomyMode: s.autonomyMode,
      setAutonomyMode: s.setAutonomyMode,
      researchEnabled: s.researchEnabled,
      setResearchEnabled: s.setResearchEnabled,
      researchTime: s.researchTime,
      setResearchTime: s.setResearchTime,
      reasoningModel: s.reasoningModel,
      setReasoningModel: s.setReasoningModel,
      cheapModel: s.cheapModel,
      setCheapModel: s.setCheapModel,
      fallbackModel: s.fallbackModel,
      setFallbackModel: s.setFallbackModel,
      openrouterKey: s.openrouterKey,
      setOpenrouterKey: s.setOpenrouterKey,
    })),
  )
  const { data: instrumentSelections, isLoading: instrumentsLoading } = useInstrumentSelections()
  const saveInstruments = useSaveInstrumentSelections()

  return (
    <div>
      <PageHeader title="Settings" subtitle="Trading mode, risk, instruments, strategies, automation & appearance" />

      <div className="flex flex-col gap-4 p-5">
        <TradingModeCard />

        <AccountGuardrailsCard />

        {instrumentsLoading ? (
          <div className="h-40 animate-pulse rounded-lg bg-muted" />
        ) : instrumentSelections ? (
          <StrategyConfigEditor
            config={instrumentSelections}
            onSave={(config) => saveInstruments.mutate(config)}
            isSaving={saveInstruments.isPending}
          />
        ) : null}

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">Automation</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-sm">Full-auto</span>
                <p className="text-xs text-muted-foreground">
                  {store.autonomyMode === 'full-auto'
                    ? 'System deploys & trades automatically'
                    : 'Semi-auto — ask before deploying new strategies (live trades still need approval on the Approve page)'}
                </p>
              </div>
              <Toggle
                checked={store.autonomyMode === 'full-auto'}
                onCheckedChange={(v) => store.setAutonomyMode(v ? 'full-auto' : 'semi-auto')}
                label="Full-auto"
              />
            </div>

            <div className="flex items-center justify-between border-t border-border pt-4">
              <div className="flex items-center gap-2">
                <FlaskConical className="size-4 text-purple-500" />
                <div>
                  <span className="text-sm">Research agent</span>
                  <p className="text-xs text-muted-foreground">Daily market research brief, ~$6/month via OpenRouter</p>
                </div>
              </div>
              <Toggle checked={store.researchEnabled} onCheckedChange={store.setResearchEnabled} label="Research agent" />
            </div>
            {store.researchEnabled && (
              <div>
                <label htmlFor="research-time" className="text-xs text-muted-foreground">
                  Run time
                </label>
                <Input
                  id="research-time"
                  type="time"
                  value={store.researchTime}
                  onChange={(e) => store.setResearchTime(e.target.value)}
                  className="h-9 max-w-[120px] text-xs"
                />
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm font-medium">
              <Brain className="size-4 text-primary" /> AI Models
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <ModelSelect
                label="Reasoning Model"
                hint="Used by Research & Learning agents"
                value={store.reasoningModel}
                onChange={store.setReasoningModel}
              />
              <ModelSelect
                label="Cheap Model"
                hint="Used for daily summaries"
                value={store.cheapModel}
                onChange={store.setCheapModel}
              />
            </div>
            <ModelSelect
              label="Fallback Model"
              hint="Used if the primary model fails"
              value={store.fallbackModel}
              onChange={store.setFallbackModel}
              className="h-9 max-w-xs text-xs"
            />
            <div>
              <label htmlFor="openrouter-key" className="flex items-center gap-1 text-xs text-muted-foreground">
                <Key className="size-3" /> OpenRouter API Key
              </label>
              <Input
                id="openrouter-key"
                type="password"
                value={store.openrouterKey}
                onChange={(e) => store.setOpenrouterKey(e.target.value)}
                placeholder="sk-or-..."
                className="h-9 max-w-sm text-xs"
              />
            </div>
          </CardContent>
        </Card>

        <AppearanceCard />
      </div>
    </div>
  )
}
