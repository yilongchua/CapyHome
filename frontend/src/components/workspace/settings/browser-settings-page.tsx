"use client";

import { CheckIcon, GlobeIcon, Loader2Icon, PlusIcon } from "lucide-react";
import { useCallback, useState } from "react";

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
  useAddMCPServer,
  useMCPConfig,
} from "@/core/mcp/hooks";
import {
  useTestGenericEndpoint,
} from "@/core/onboarding";

import { SettingsSection } from "./settings-section";

export function BrowserSettingsPage() {
  const { t } = useI18n();

  const [customUrl, setCustomUrl] = useState("");

  const { config: mcpConfig } = useMCPConfig();
  const { mutate: addMCPServer, isPending: addingMcp } = useAddMCPServer();
  const { mutate: testEndpoint, data: testResult, isPending: testing, reset: resetTest } = useTestGenericEndpoint();

  const playwrightExists = mcpConfig?.mcp_servers?.playwright != null;

  const handleQuickAdd = useCallback(() => {
    addMCPServer(
      {
        serverName: "playwright",
        serverConfig: {
          enabled: true,
          description: "Playwright MCP server for browser automation (web scraping, form filling, etc.)",
          type: "stdio",
          command: "npx",
          args: ["-y", "@playwright/mcp"],
          env: {},
          excluded_tools: [],
        },
      },
      {
        onSuccess: () => {
          setCustomUrl("");
        },
      },
    );
  }, [addMCPServer]);

  const handleTestUrl = useCallback(() => {
    if (!customUrl.trim()) return;
    resetTest();
    testEndpoint({ url: customUrl.trim() });
  }, [customUrl, testEndpoint, resetTest]);

  const handleAddAsMcp = useCallback(() => {
    if (!customUrl.trim()) return;
    const existing = mcpConfig?.mcp_servers ?? {};
    const base = "playwright-sse";
    let serverName = base;
    if (serverName in existing) {
      let idx = 2;
      while (`${base}-${idx}` in existing) idx += 1;
      serverName = `${base}-${idx}`;
    }
    addMCPServer(
      {
        serverName,
        serverConfig: {
          enabled: true,
          description: "Playwright MCP server (SSE/HTTP mode)",
          type: "http",
          url: customUrl.trim(),
          excluded_tools: [],
        },
      },
      {
        onSuccess: () => {
          setCustomUrl("");
          resetTest();
        },
      },
    );
  }, [customUrl, addMCPServer, resetTest, mcpConfig]);

  return (
    <SettingsSection
      title={t.settings.browser.title}
      description={t.settings.browser.description}
    >
      <div className="space-y-6">
        {/* Quick-add Playwright MCP */}
        <div className="space-y-3">
          <div>
            <p className="text-muted-foreground text-sm">
              {t.settings.browser.quickAddDescription}
            </p>
          </div>
          {playwrightExists ? (
            <Item className="w-full" variant="outline">
              <ItemContent>
                <ItemTitle>
                  <GlobeIcon className="size-4" />
                  <span>Playwright MCP</span>
                </ItemTitle>
                <ItemDescription>
                  Browser automation via Playwright MCP (stdio: npx @playwright/mcp)
                </ItemDescription>
              </ItemContent>
              <ItemActions>
                <span className="text-muted-foreground text-xs">
                  {t.settings.tools.editServer}
                </span>
              </ItemActions>
            </Item>
          ) : (
            <div>
              <Button
                size="sm"
                disabled={addingMcp}
                onClick={handleQuickAdd}
              >
                {addingMcp ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <PlusIcon className="size-3.5" />
                )}
                {t.settings.browser.quickAddButton}
              </Button>
            </div>
          )}
        </div>

        {/* Manual URL entry for SSE/HTTP */}
        <div className="space-y-3">
          <div>
            <div className="text-sm font-medium">{t.settings.browser.manualTitle}</div>
            <p className="text-muted-foreground text-xs">
              {t.settings.browser.manualDescription}
            </p>
          </div>
          <div className="flex gap-2">
            <input
              className="border-input bg-background focus-visible:ring-ring flex-1 rounded-md border px-3 py-1.5 text-sm focus-visible:ring-1 focus-visible:outline-none"
              placeholder={t.settings.browser.urlPlaceholder}
              value={customUrl}
              onChange={(e) => {
                setCustomUrl(e.target.value);
                resetTest();
              }}
            />
            <Button
              size="sm"
              variant="outline"
              disabled={!customUrl.trim() || testing}
              onClick={handleTestUrl}
            >
              {testing ? (
                <Loader2Icon className="size-3.5 animate-spin" />
              ) : (
                <CheckIcon className="size-3.5" />
              )}
              {testing ? t.settings.browser.testing : t.settings.browser.testConnection}
            </Button>
          </div>

          {testResult && !testResult.ok && (
            <div className="text-destructive text-sm">
              {t.settings.browser.connectionFailed}: {testResult.error}
            </div>
          )}
          {testResult?.ok && (
            <div className="text-green-600 text-sm">
              {t.settings.browser.connectionSuccess}
              {testResult.status_code && (
                <span className="text-muted-foreground ml-1">
                  (HTTP {testResult.status_code})
                </span>
              )}
            </div>
          )}

          {testResult?.ok && customUrl.trim() && (
            <Button size="sm" variant="outline" onClick={handleAddAsMcp}>
              <PlusIcon className="size-3.5" />
              {t.settings.browser.addAsMcp}
            </Button>
          )}
        </div>
      </div>
    </SettingsSection>
  );
}
