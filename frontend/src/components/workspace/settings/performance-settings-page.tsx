"use client";

import { AlertTriangleIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsSection } from "./settings-section";

const MIN_WORKERS = 1;
const MAX_WORKERS = 20;

async function fetchWorkers(): Promise<number> {
  const res = await fetch(`${getBackendBaseURL()}/api/system/workers`);
  if (!res.ok) throw new Error("Failed to load worker config");
  const data = await res.json();
  return data.workers as number;
}

async function saveWorkers(workers: number): Promise<void> {
  const res = await fetch(`${getBackendBaseURL()}/api/system/workers`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workers }),
  });
  if (!res.ok) throw new Error("Failed to save worker config");
}

async function triggerRestart(mode: "dev" | "start"): Promise<void> {
  const res = await fetch(`${getBackendBaseURL()}/api/system/restart`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  if (!res.ok) throw new Error("Failed to trigger restart");
}

export function PerformanceSettingsPage() {
  const { t } = useI18n();
  const [loaded, setLoaded] = useState<number | null>(null);
  const [input, setInput] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [restartRequired, setRestartRequired] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [restarting, setRestarting] = useState<"dev" | "start" | null>(null);
  const [serverRestarting, setServerRestarting] = useState(false);
  const [restartError, setRestartError] = useState<string | null>(null);

  useEffect(() => {
    fetchWorkers()
      .then((n) => {
        setLoaded(n);
        setInput(String(n));
      })
      .catch(() => {
        setLoaded(5);
        setInput("5");
      });
  }, []);

  const parsed = parseInt(input, 10);
  const valid = !isNaN(parsed) && parsed >= MIN_WORKERS && parsed <= MAX_WORKERS;
  const dirty = valid && parsed !== loaded;

  const handleSave = async () => {
    if (!valid || !dirty) return;
    setSaving(true);
    setError(null);
    try {
      await saveWorkers(parsed);
      setLoaded(parsed);
      setRestartRequired(true);
    } catch {
      setError(t.settings.performance.saveError);
    } finally {
      setSaving(false);
    }
  };

  const handleRestart = async (mode: "dev" | "start") => {
    setRestarting(mode);
    setRestartError(null);
    try {
      await triggerRestart(mode);
      setServerRestarting(true);
    } catch {
      setRestartError(t.settings.performance.restartError);
    } finally {
      setRestarting(null);
    }
  };

  return (
    <>
    <SettingsSection
      title={t.settings.performance.workerSlotsTitle}
      description={t.settings.performance.workerSlotsDescription}
    >
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-3">
          <Input
            type="number"
            min={MIN_WORKERS}
            max={MAX_WORKERS}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              setRestartRequired(false);
            }}
            className="w-24"
            disabled={loaded === null || saving}
          />
          <Button
            onClick={handleSave}
            disabled={!dirty || saving}
            size="sm"
          >
            {saving ? t.settings.performance.saving : t.settings.performance.save}
          </Button>
        </div>

        {error && (
          <p className="text-destructive text-sm">{error}</p>
        )}

        {restartRequired && (
          <div className="flex items-start gap-2 rounded-md border border-yellow-400/40 bg-yellow-400/10 px-3 py-2.5 text-sm text-yellow-700 dark:text-yellow-400">
            <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
            <span>{t.settings.performance.restartRequired}</span>
          </div>
        )}

        <p className="text-muted-foreground text-xs">
          {t.settings.performance.rangeHint(MIN_WORKERS, MAX_WORKERS)}
        </p>
      </div>
    </SettingsSection>

    <SettingsSection
      title={t.settings.performance.restartSectionTitle}
      description={t.settings.performance.restartSectionDescription}
    >
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleRestart("dev")}
            disabled={restarting !== null}
          >
            {restarting === "dev"
              ? t.settings.performance.restarting
              : t.settings.performance.restartDev}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleRestart("start")}
            disabled={restarting !== null}
          >
            {restarting === "start"
              ? t.settings.performance.restarting
              : t.settings.performance.restartStart}
          </Button>
        </div>

        {restartError && (
          <p className="text-destructive text-sm">{restartError}</p>
        )}

        {serverRestarting && (
          <div className="flex items-start gap-2 rounded-md border border-yellow-400/40 bg-yellow-400/10 px-3 py-2.5 text-sm text-yellow-700 dark:text-yellow-400">
            <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
            <span>{t.settings.performance.restartingBanner}</span>
          </div>
        )}
      </div>
    </SettingsSection>
    </>
  );
}
