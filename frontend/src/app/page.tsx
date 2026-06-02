"use client";

import Link from "next/link";

import { useThemeAssets } from "@/hooks/use-theme-assets";

export default function HomePage() {
  const asset = useThemeAssets();
  return (
    <Link
      href="/workspace/chats/new"
      aria-label="Start a new chat"
      className="fixed inset-0 block bg-contain bg-center bg-no-repeat"
      style={{ backgroundImage: `url('${asset("main-landing.webp")}')` }}
    >
      <span className="sr-only">Start a new chat</span>
    </Link>
  );
}
