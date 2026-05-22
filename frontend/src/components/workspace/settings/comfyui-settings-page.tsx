"use client";

import { CheckIcon, ImageIcon, Loader2Icon } from "lucide-react";
import { useCallback, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemTitle,
} from "@/components/ui/item";
import { Switch } from "@/components/ui/switch";
import { useCommunityTools, useToggleCommunityTool } from "@/core/community-tools";
import { useI18n } from "@/core/i18n/hooks";
import { useTestComfyuiEndpoint } from "@/core/onboarding";

import { SettingsSection } from "./settings-section";

export function ComfyuiSettingsPage() {
  const { t } = useI18n();

  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8188");

  const { mutate: testEndpoint, data: testResult, isPending: testing, reset: resetTest } = useTestComfyuiEndpoint();
  const { tools, isLoading: loadingTools } = useCommunityTools();
  const { mutate: toggleTool } = useToggleCommunityTool();

  const comfyuiTool = tools.find((tool) => tool.name === "comfyui_generate");
  const isEnabled = comfyuiTool?.enabled ?? false;

  const handleTest = useCallback(() => {
    if (!baseUrl.trim()) return;
    resetTest();
    testEndpoint({ baseUrl: baseUrl.trim() });
  }, [baseUrl, testEndpoint, resetTest]);

  function handleToggle(checked: boolean) {
    toggleTool({ name: "comfyui_generate", enabled: checked });
  }

  return (
    <SettingsSection
      title={t.settings.comfyui.title}
      description={t.settings.comfyui.description}
    >
      <div className="space-y-4">
        {/* Base URL input */}
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium">{t.settings.comfyui.baseUrl}</label>
          <div className="flex gap-2">
            <input
              className="border-input bg-background focus-visible:ring-ring flex-1 rounded-md border px-3 py-1.5 text-sm focus-visible:ring-1 focus-visible:outline-none"
              placeholder={t.settings.comfyui.baseUrlPlaceholder}
              value={baseUrl}
              onChange={(e) => {
                setBaseUrl(e.target.value);
                resetTest();
              }}
            />
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
              {testing ? t.settings.comfyui.testing : t.settings.comfyui.testConnection}
            </Button>
          </div>
        </div>

        {/* Test result */}
        {testResult && !testResult.ok && (
          <div className="text-destructive text-sm">
            {t.settings.comfyui.connectionFailed}: {testResult.error}
          </div>
        )}
        {testResult?.ok && (
          <div className="text-green-600 text-sm">
            {t.settings.comfyui.connectionSuccess}
          </div>
        )}

        {/* ComfyUI tool toggle */}
        {loadingTools ? (
          <div className="text-muted-foreground text-sm">{t.common.loading}</div>
        ) : (
          <Item className="w-full" variant="outline">
            <ItemContent>
              <ItemTitle>
                <ImageIcon className="size-4" />
                <span>ComfyUI Generate</span>
              </ItemTitle>
              <ItemDescription>
                {t.settings.comfyui.enableToolDescription}
              </ItemDescription>
              <p className="text-muted-foreground mt-1 text-xs">
                {isEnabled
                  ? t.settings.comfyui.toolEnabled
                  : t.settings.comfyui.toolDisabled}
              </p>
            </ItemContent>
            <ItemActions>
              <Switch
                checked={isEnabled}
                onCheckedChange={handleToggle}
              />
            </ItemActions>
          </Item>
        )}
      </div>
    </SettingsSection>
  );
}
