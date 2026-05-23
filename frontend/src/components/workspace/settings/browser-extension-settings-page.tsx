"use client";

import {
  BookmarkIcon,
  CheckIcon,
  ChromeIcon,
  CopyIcon,
  FolderOpenIcon,
  KeyboardIcon,
  MousePointerClickIcon,
  PuzzleIcon,
  ServerIcon,
  ToggleRightIcon,
  ZapIcon,
} from "lucide-react";
import { useCallback, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Item,
  ItemContent,
  ItemDescription,
  ItemTitle,
} from "@/components/ui/item";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsSection } from "./settings-section";

const EXTENSION_REPO_PATH = "browser_extensions/knowledge-vault-clipper";
const CHROME_EXTENSIONS_URL = "chrome://extensions";

export function BrowserExtensionSettingsPage() {
  const { t } = useI18n();
  const [copiedPath, setCopiedPath] = useState(false);
  const [copiedShortcut, setCopiedShortcut] = useState(false);

  const handleCopy = useCallback(
    async (value: string, setter: (v: boolean) => void) => {
      try {
        await navigator.clipboard.writeText(value);
        setter(true);
        setTimeout(() => setter(false), 2000);
      } catch {
        // clipboard may be unavailable; ignore
      }
    },
    [],
  );

  const steps = [
    {
      icon: ChromeIcon,
      title: t.settings.browserExtension.step1Title,
      description: t.settings.browserExtension.step1Description,
      action: (
        <div className="flex items-center gap-2">
          <code className="bg-muted text-foreground rounded px-2 py-1 text-xs">
            {CHROME_EXTENSIONS_URL}
          </code>
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              handleCopy(CHROME_EXTENSIONS_URL, setCopiedShortcut)
            }
          >
            {copiedShortcut ? (
              <CheckIcon className="size-3.5" />
            ) : (
              <CopyIcon className="size-3.5" />
            )}
            {copiedShortcut
              ? t.settings.browserExtension.copied
              : t.settings.browserExtension.copyUrl}
          </Button>
        </div>
      ),
    },
    {
      icon: ToggleRightIcon,
      title: t.settings.browserExtension.step2Title,
      description: t.settings.browserExtension.step2Description,
    },
    {
      icon: FolderOpenIcon,
      title: t.settings.browserExtension.step3Title,
      description: t.settings.browserExtension.step3Description,
      action: (
        <div className="flex items-center gap-2">
          <code className="bg-muted text-foreground rounded px-2 py-1 text-xs">
            {EXTENSION_REPO_PATH}
          </code>
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              handleCopy(EXTENSION_REPO_PATH, setCopiedPath)
            }
          >
            {copiedPath ? (
              <CheckIcon className="size-3.5" />
            ) : (
              <CopyIcon className="size-3.5" />
            )}
            {copiedPath
              ? t.settings.browserExtension.copied
              : t.settings.browserExtension.copyPath}
          </Button>
        </div>
      ),
    },
    {
      icon: ServerIcon,
      title: t.settings.browserExtension.step4Title,
      description: t.settings.browserExtension.step4Description,
    },
  ];

  return (
    <SettingsSection
      title={t.settings.browserExtension.title}
      description={t.settings.browserExtension.description}
    >
      <div className="space-y-6">
        <Item className="w-full" variant="outline">
          <ItemContent>
            <ItemTitle>
              <PuzzleIcon className="size-4" />
              <span>{t.settings.browserExtension.aboutTitle}</span>
            </ItemTitle>
            <ItemDescription>
              {t.settings.browserExtension.aboutDescription}
            </ItemDescription>
          </ItemContent>
        </Item>

        <div className="border-primary/30 bg-primary/5 space-y-2 rounded-md border p-3">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <ZapIcon className="text-primary size-4" />
            <span>{t.settings.browserExtension.autoClipTitle}</span>
            <span className="bg-primary text-primary-foreground ml-auto rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide">
              {t.settings.browserExtension.autoClipDefaultBadge}
            </span>
          </div>
          <p className="text-muted-foreground text-xs leading-relaxed">
            {t.settings.browserExtension.autoClipDescription}
          </p>
          <ul className="text-muted-foreground space-y-1 text-xs">
            <li>• {t.settings.browserExtension.autoClipDwellNote}</li>
            <li>• {t.settings.browserExtension.autoClipBlocklistNote}</li>
            <li>• {t.settings.browserExtension.autoClipDedupNote}</li>
            <li>• {t.settings.browserExtension.autoClipOptOutNote}</li>
          </ul>
        </div>

        <div className="border-border bg-muted/40 space-y-1 rounded-md border p-3 text-xs">
          <div className="flex items-center gap-2 font-semibold">
            <CheckIcon className="text-primary size-4" />
            <span>{t.settings.browserExtension.queueSignTitle}</span>
          </div>
          <p className="text-muted-foreground leading-relaxed">
            {t.settings.browserExtension.queueSignBody}
          </p>
        </div>

        <div className="space-y-3">
          <div className="text-sm font-medium">
            {t.settings.browserExtension.installTitle}
          </div>
          <ol className="space-y-3">
            {steps.map((step, idx) => {
              const Icon = step.icon;
              return (
                <li
                  key={idx}
                  className="border-border flex gap-3 rounded-md border p-3"
                >
                  <div className="bg-primary text-primary-foreground flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold">
                    {idx + 1}
                  </div>
                  <div className="min-w-0 flex-1 space-y-2">
                    <div className="flex items-center gap-2 text-sm font-medium">
                      <Icon className="text-muted-foreground size-4" />
                      <span>{step.title}</span>
                    </div>
                    <p className="text-muted-foreground text-xs leading-relaxed">
                      {step.description}
                    </p>
                    {step.action && <div className="pt-1">{step.action}</div>}
                  </div>
                </li>
              );
            })}
          </ol>
        </div>

        <div className="space-y-3">
          <div className="text-sm font-medium">
            {t.settings.browserExtension.usageTitle}
          </div>
          <ul className="space-y-2 text-sm">
            <li className="text-muted-foreground flex items-start gap-2">
              <MousePointerClickIcon className="mt-0.5 size-4 shrink-0" />
              <span>{t.settings.browserExtension.usageClick}</span>
            </li>
            <li className="text-muted-foreground flex items-start gap-2">
              <BookmarkIcon className="mt-0.5 size-4 shrink-0" />
              <span>{t.settings.browserExtension.usageRightClick}</span>
            </li>
            <li className="text-muted-foreground flex items-start gap-2">
              <KeyboardIcon className="mt-0.5 size-4 shrink-0" />
              <span className="space-x-1">
                <span>{t.settings.browserExtension.usageShortcut}</span>
                <code className="bg-muted text-foreground rounded px-1.5 py-0.5 text-xs">
                  {t.settings.browserExtension.shortcutMac}
                </code>
                <span className="text-muted-foreground">
                  {t.settings.browserExtension.shortcutOr}
                </span>
                <code className="bg-muted text-foreground rounded px-1.5 py-0.5 text-xs">
                  {t.settings.browserExtension.shortcutWin}
                </code>
              </span>
            </li>
          </ul>
        </div>

        <div className="bg-muted/50 rounded-md border p-3 text-xs">
          <div className="mb-1 font-medium">
            {t.settings.browserExtension.troubleshootingTitle}
          </div>
          <p className="text-muted-foreground leading-relaxed">
            {t.settings.browserExtension.troubleshootingBody}
          </p>
        </div>
      </div>
    </SettingsSection>
  );
}
