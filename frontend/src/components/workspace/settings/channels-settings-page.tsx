"use client";

import { AlertTriangleIcon, CheckCircle2Icon, Loader2Icon, SendIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { getBackendBaseURL } from "@/core/config";

import { SettingsSection } from "./settings-section";

type TelegramConfig = {
  enabled: boolean;
  bot_token: string;
  allowed_users: number[];
};

type ChannelStatus = {
  service_running: boolean;
  channels: Record<string, { enabled: boolean; running: boolean }>;
};

async function fetchConfig(): Promise<TelegramConfig> {
  const res = await fetch(`${getBackendBaseURL()}/api/channels/config`);
  if (!res.ok) throw new Error("Failed to load channel config");
  const data = await res.json();
  return data.telegram as TelegramConfig;
}

async function fetchStatus(): Promise<ChannelStatus> {
  const res = await fetch(`${getBackendBaseURL()}/api/channels/`);
  if (!res.ok) throw new Error("Failed to load channel status");
  return res.json() as Promise<ChannelStatus>;
}

async function saveConfig(
  cfg: TelegramConfig,
): Promise<{ restarted: boolean; message: string }> {
  const res = await fetch(`${getBackendBaseURL()}/api/channels/config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ telegram: cfg }),
  });
  if (!res.ok) throw new Error("Failed to save channel config");
  return res.json() as Promise<{ restarted: boolean; message: string }>;
}

function parseAllowedUsers(input: string): number[] {
  return input
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => Number.parseInt(s, 10))
    .filter((n) => Number.isFinite(n));
}

export function ChannelsSettingsPage() {
  const [loaded, setLoaded] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [token, setToken] = useState("");
  const [allowedUsers, setAllowedUsers] = useState("");
  const [running, setRunning] = useState<boolean | null>(null);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refreshStatus = () =>
    fetchStatus()
      .then((s) => setRunning(Boolean(s.channels?.telegram?.running)))
      .catch(() => setRunning(null));

  useEffect(() => {
    fetchConfig()
      .then((cfg) => {
        setEnabled(cfg.enabled);
        setToken(cfg.bot_token);
        setAllowedUsers(cfg.allowed_users.join(", "));
      })
      .catch(() => {
        /* leave defaults */
      })
      .finally(() => setLoaded(true));
    void refreshStatus();
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const result = await saveConfig({
        enabled,
        bot_token: token.trim(),
        allowed_users: parseAllowedUsers(allowedUsers),
      });
      setNotice(result.message);
      await refreshStatus();
    } catch {
      setError("Failed to save. Check that the gateway is running.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsSection
      title="Channels"
      description="Connect a Telegram bot so you can chat with CapyHome and receive notifications from your phone."
    >
      <div className="flex flex-col gap-6">
        {/* Telegram block */}
        <div className="rounded-lg border p-4 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 font-medium">
              <SendIcon className="size-4" />
              Telegram
            </div>
            {running === null ? null : running ? (
              <Badge variant="secondary" className="gap-1">
                <CheckCircle2Icon className="size-3.5" /> Running
              </Badge>
            ) : (
              <Badge variant="outline" className="text-muted-foreground">
                Stopped
              </Badge>
            )}
          </div>

          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-sm font-medium">Enable bot</div>
              <div className="text-muted-foreground text-xs">
                Starts long-polling — no public URL needed.
              </div>
            </div>
            <Switch
              checked={enabled}
              onCheckedChange={setEnabled}
              disabled={!loaded || saving}
            />
          </div>

          <div className="space-y-1.5">
            <div className="text-sm font-medium">Bot token</div>
            <Input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="123456789:ABCdef..."
              autoComplete="off"
              disabled={!loaded || saving}
            />
            <div className="text-muted-foreground text-xs">
              Create a bot with{" "}
              <span className="font-mono">@BotFather</span> on Telegram and paste
              the token here. Stored locally in{" "}
              <span className="font-mono">extensions_config.json</span>.
            </div>
          </div>

          <div className="space-y-1.5">
            <div className="text-sm font-medium">
              Allowed user IDs{" "}
              <span className="text-muted-foreground font-normal">(optional)</span>
            </div>
            <Input
              value={allowedUsers}
              onChange={(e) => setAllowedUsers(e.target.value)}
              placeholder="123456789, 987654321"
              disabled={!loaded || saving}
            />
            <div className="text-muted-foreground text-xs">
              Comma-separated Telegram user IDs allowed to use the bot. Leave
              empty to allow anyone who finds it (not recommended).
            </div>
          </div>

          <div className="flex items-center gap-3">
            <Button onClick={handleSave} disabled={!loaded || saving} size="sm">
              {saving ? (
                <>
                  <Loader2Icon className="size-4 animate-spin" /> Saving…
                </>
              ) : (
                "Save & apply"
              )}
            </Button>
          </div>

          {error && <p className="text-destructive text-sm">{error}</p>}
          {notice && (
            <p className="text-muted-foreground text-sm">{notice}</p>
          )}

          {enabled && !token.trim() && (
            <div className="flex items-start gap-2 rounded-md border border-yellow-400/40 bg-yellow-400/10 px-3 py-2.5 text-sm text-yellow-700 dark:text-yellow-400">
              <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
              <span>
                Enabled without a bot token — the channel won&apos;t start until
                you add one.
              </span>
            </div>
          )}
        </div>

        <p className="text-muted-foreground text-xs">
          After enabling, message your bot once (e.g. <span className="font-mono">/start</span>)
          so CapyHome knows where to send proactive notifications.
        </p>
      </div>
    </SettingsSection>
  );
}
