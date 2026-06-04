"use client";

import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

const THEME_ASSET_FOLDER: Record<string, string> = {
  capyhome: "CapyHome",
  accenture: "Accenture",
  light: "CapyHome",
  dark: "CapyHome",
};

export function useThemeAssets() {
  const { theme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Use default folder until mounted to match SSR output and avoid hydration mismatch.
  // next-themes resolves the theme from localStorage only on the client.
  const folder = mounted ? (THEME_ASSET_FOLDER[theme ?? "capyhome"] ?? "CapyHome") : "CapyHome";
  return (filename: string) => `/${folder}/${filename}`;
}
