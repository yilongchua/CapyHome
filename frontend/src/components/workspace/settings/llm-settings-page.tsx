"use client";

import {
  BotIcon,
  CheckIcon,
  CpuIcon,
  FlaskConicalIcon,
  Loader2Icon,
  PlusIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";
import { useCallback, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemTitle,
} from "@/components/ui/item";
import { useI18n } from "@/core/i18n/hooks";
import {
  useLlmEndpoints,
  useSaveLlmEndpoints,
  useTestLlmEndpoint,
} from "@/core/onboarding";
import type { UserLlmEndpoint } from "@/core/onboarding/types";
import { cn } from "@/lib/utils";

import { SettingsSection } from "./settings-section";

type ProviderType = "ollama" | "lm-studio" | "custom";

const PROVIDER_DEFAULTS: Record<
  ProviderType,
  { baseUrl: string; icon: React.ReactNode; label: string }
> = {
  ollama: {
    baseUrl: "http://localhost:11434/v1",
    icon: <FlaskConicalIcon className="size-4" />,
    label: "Ollama",
  },
  "lm-studio": {
    baseUrl: "http://localhost:1234/v1",
    icon: <CpuIcon className="size-4" />,
    label: "LM Studio",
  },
  custom: {
    baseUrl: "",
    icon: <BotIcon className="size-4" />,
    label: "Custom",
  },
};

export function LlmSettingsPage() {
  const { t } = useI18n();

  const [provider, setProvider] = useState<ProviderType>("ollama");
  const [displayName, setDisplayName] = useState("");
  const [baseUrl, setBaseUrl] = useState(PROVIDER_DEFAULTS.ollama.baseUrl);
  const [apiKey, setApiKey] = useState("");
  const [selectedModels, setSelectedModels] = useState<string[]>([]);
  const [editingKey, setEditingKey] = useState<string | null>(null);

  const { endpoints, isLoading: loadingEndpoints } = useLlmEndpoints();
  const { mutate: testEndpoint, data: testResult, isPending: testing, reset: resetTest } = useTestLlmEndpoint();
  const { mutate: saveEndpoints, isPending: saving } = useSaveLlmEndpoints();

  function handleProviderChange(p: ProviderType) {
    setProvider(p);
    setBaseUrl(PROVIDER_DEFAULTS[p].baseUrl);
    resetTest();
    setSelectedModels([]);
  }

  const handleTest = useCallback(() => {
    if (!baseUrl.trim()) return;
    resetTest();
    testEndpoint({ baseUrl: baseUrl.trim(), apiKey });
  }, [baseUrl, apiKey, testEndpoint, resetTest]);

  function toggleModel(modelId: string) {
    setSelectedModels((prev) =>
      prev.includes(modelId)
        ? prev.filter((m) => m !== modelId)
        : [...prev, modelId],
    );
  }

  function buildEndpointKey(): string {
    const base = displayName.trim().toLowerCase().replace(/\s+/g, "-") || provider;
    if (!(base in endpoints)) return base;
    let idx = 2;
    while (`${base}-${idx}` in endpoints) {
      idx += 1;
    }
    return `${base}-${idx}`;
  }

  function handleAdd() {
    if (!displayName.trim() || !baseUrl.trim()) return;
    const key = editingKey ?? buildEndpointKey();
    const updated: Record<string, UserLlmEndpoint> = {
      ...endpoints,
      [key]: {
        enabled: true,
        provider,
        display_name: displayName.trim(),
        base_url: baseUrl.trim(),
        api_key: apiKey,
        models: selectedModels,
        default_model: selectedModels[0] ?? "",
        supports_thinking: false,
        supports_vision: false,
      },
    };
    saveEndpoints(updated, {
      onSuccess: () => {
        resetForm();
      },
    });
  }

  function resetForm() {
    setProvider("ollama");
    setDisplayName("");
    setBaseUrl(PROVIDER_DEFAULTS.ollama.baseUrl);
    setApiKey("");
    setSelectedModels([]);
    setEditingKey(null);
    resetTest();
  }

  function handleEdit(key: string, ep: UserLlmEndpoint) {
    setEditingKey(key);
    setProvider(ep.provider as ProviderType);
    setDisplayName(ep.display_name);
    setBaseUrl(ep.base_url);
    setApiKey(ep.api_key);
    setSelectedModels(ep.models);
    resetTest();
  }

  function handleDelete(key: string) {
    if (!confirm(t.settings.llm.deleteConfirm)) return;
    const updated = { ...endpoints };
    delete updated[key];
    saveEndpoints(updated);
  }

  function handleToggle(key: string, ep: UserLlmEndpoint) {
    saveEndpoints({
      ...endpoints,
      [key]: { ...ep, enabled: !ep.enabled },
    });
  }

  const canAdd = !!displayName.trim() && !!baseUrl.trim();
  const isEditing = editingKey !== null;

  return (
    <SettingsSection
      title={t.settings.llm.title}
      description={t.settings.llm.description}
    >
      {/* Provider selector */}
      <div className="mb-4">
        <label className="text-sm font-medium mb-2 block">
          {t.settings.llm.providerType}
        </label>
        <div className="flex gap-2">
          {(Object.entries(PROVIDER_DEFAULTS) as [ProviderType, typeof PROVIDER_DEFAULTS[ProviderType]][]).map(
            ([key, cfg]) => (
              <button
                key={key}
                type="button"
                onClick={() => handleProviderChange(key)}
                className={cn(
                  "flex items-center gap-2 rounded-md border px-4 py-2.5 text-sm font-medium transition-colors",
                  provider === key
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border hover:bg-muted text-muted-foreground",
                )}
              >
                {cfg.icon}
                {cfg.label}
              </button>
            ),
          )}
        </div>
      </div>

      {/* Form fields */}
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium">{t.settings.llm.displayName}</label>
          <input
            className="border-input bg-background focus-visible:ring-ring w-full rounded-md border px-3 py-1.5 text-sm focus-visible:ring-1 focus-visible:outline-none"
            placeholder={t.settings.llm.displayNamePlaceholder}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium">{t.settings.llm.baseUrl}</label>
          <input
            className="border-input bg-background focus-visible:ring-ring w-full rounded-md border px-3 py-1.5 text-sm focus-visible:ring-1 focus-visible:outline-none"
            placeholder={t.settings.llm.baseUrlPlaceholder}
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium">{t.settings.llm.apiKey}</label>
          <input
            className="border-input bg-background focus-visible:ring-ring w-full rounded-md border px-3 py-1.5 text-sm focus-visible:ring-1 focus-visible:outline-none"
            placeholder={t.settings.llm.apiKeyPlaceholder}
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </div>
      </div>

      {/* Test + Add actions */}
      <div className="mb-4 flex items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={!baseUrl.trim() || testing}
          onClick={handleTest}
        >
          {testing ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : (
            <CheckIcon className="size-3.5" />
          )}
          {testing ? t.settings.llm.testing : t.settings.llm.testConnection}
        </Button>
        <Button
          size="sm"
          disabled={!canAdd || saving}
          onClick={handleAdd}
        >
          <PlusIcon className="size-3.5" />
          {isEditing ? t.settings.llm.saveProvider : t.settings.llm.addProvider}
        </Button>
        {(editingKey !== null || displayName || baseUrl) && (
          <Button size="sm" variant="ghost" onClick={resetForm}>
            <XIcon className="size-3.5" />
          </Button>
        )}
      </div>

      {/* Test result: discovered models */}
      {testResult && !testResult.ok && (
        <div className="text-destructive mb-3 text-sm">
          {t.settings.llm.connectionFailed}: {testResult.error}
        </div>
      )}
      {testResult?.ok && (
        <div className="bg-muted/50 mb-4 rounded-md p-3">
          <p className="text-muted-foreground mb-2 text-xs font-medium">
            {t.settings.llm.discoveredModels(testResult.models.length)}
          </p>
          {testResult.models.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              {t.settings.llm.connectionSuccess}
            </p>
          ) : (
            <div className="flex max-h-40 flex-col gap-1.5 overflow-y-auto">
              {testResult.models.map((modelId) => (
                <label
                  key={modelId}
                  className="flex cursor-pointer items-center gap-2 rounded-md border px-3 py-1.5 text-sm"
                >
                  <input
                    type="checkbox"
                    className="cursor-pointer"
                    checked={selectedModels.includes(modelId)}
                    onChange={() => toggleModel(modelId)}
                  />
                  <span className="font-mono text-xs">{modelId}</span>
                </label>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Existing endpoints */}
      <div className="mt-6">
        <h3 className="text-sm font-medium mb-3">
          {t.settings.llm.configuredEndpoints}
        </h3>
        {loadingEndpoints && (
          <div className="text-muted-foreground text-sm">{t.common.loading}</div>
        )}
        {!loadingEndpoints && Object.keys(endpoints).length === 0 && (
          <p className="text-muted-foreground text-sm">
            {t.settings.llm.noEndpoints}
          </p>
        )}
        <div className="flex flex-col gap-2">
          {Object.entries(endpoints).map(([key, ep]) => {
            const provCfg = PROVIDER_DEFAULTS[ep.provider as ProviderType] ?? PROVIDER_DEFAULTS.custom;
            return (
              <Item key={key} className="w-full" variant="outline">
                <ItemContent>
                  <ItemTitle>
                    <span>{ep.display_name}</span>
                    <Badge variant="outline" className="text-muted-foreground text-xs">
                      {provCfg.label}
                    </Badge>
                    <Badge
                      variant="outline"
                      className={cn(
                        "text-xs",
                        ep.enabled
                          ? "border-green-500/30 text-green-600"
                          : "text-muted-foreground",
                      )}
                    >
                      {ep.enabled
                        ? t.settings.llm.endpointEnabled
                        : t.settings.llm.endpointDisabled}
                    </Badge>
                  </ItemTitle>
                  <ItemDescription className="font-mono text-xs">
                    {ep.base_url}
                  </ItemDescription>
                </ItemContent>
                <ItemActions>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => handleToggle(key, ep)}
                  >
                    <CheckIcon
                      className={cn(
                        "size-3.5",
                        ep.enabled ? "text-green-600" : "text-muted-foreground",
                      )}
                    />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-muted-foreground"
                    onClick={() => handleEdit(key, ep)}
                  >
                    {t.settings.tools.editServer}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-destructive"
                    onClick={() => handleDelete(key)}
                  >
                    <Trash2Icon className="size-3.5" />
                  </Button>
                </ItemActions>
              </Item>
            );
          })}
        </div>
      </div>
    </SettingsSection>
  );
}
