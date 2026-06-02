"use client";

import { useTheme } from "next-themes";

const THEME_ASSET_FOLDER: Record<string, string> = {
  capyhome: "CapyHome",
  accenture: "Accenture",
  light: "CapyHome",
  dark: "CapyHome",
};

export function useThemeAssets() {
  const { theme } = useTheme();
  const folder = THEME_ASSET_FOLDER[theme ?? "capyhome"] ?? "CapyHome";
  return (filename: string) => `/${folder}/${filename}`;
}
