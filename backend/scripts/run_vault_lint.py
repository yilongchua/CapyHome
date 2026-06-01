#!/usr/bin/env python
"""Standalone Knowledge Vault health-scan lint runner.

Runs VaultLearningManager.lint_vault() out-of-process — no langgraph, no event
loop to block, no reload churn. This is the read-only/diagnostic Path A scan
(orphans, stale syntheses, missing backlinks, contradictions, open questions).
It performs NO LLM calls; it only reads files + the manifest and writes a
report under 03_ops/reports/lint/ plus manifest.last_lint_at.

Usage (from backend/):
    PYTHONPATH=. .venv/bin/python scripts/run_vault_lint.py [--days N] [--vault-root PATH]
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from src.control_plane.vault_learning import VaultLearningManager


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the knowledge-vault health-scan lint standalone.")
    ap.add_argument("--days", type=int, default=30, help="freshness_window_days (default: 30)")
    ap.add_argument("--vault-root", type=str, default=None, help="override vault root path")
    args = ap.parse_args()

    if args.vault_root:
        vault_root = Path(args.vault_root)
    else:
        vault_root = VaultLearningManager.default_vault_root()

    manager = VaultLearningManager(vault_root=vault_root)
    print(f"vault_root      : {manager.vault_root}")
    print(f"freshness_window: {args.days} days")
    print("running lint_vault() ...", flush=True)

    t0 = time.perf_counter()
    report = manager.lint_vault(freshness_window_days=args.days)
    dt = time.perf_counter() - t0

    print(f"\ncompleted in {dt:.2f}s")
    print("-" * 48)
    for key in (
        "stale_syntheses_count",
        "orphan_pages_count",
        "missing_backlinks_count",
        "contradictions_count",
        "open_questions_count",
        "expired_queries_count",
        "queue_backlog_count",
    ):
        print(f"{key:24s}: {report.get(key)}")
    print("-" * 48)
    report_dir = manager.lint_reports_dir
    latest = sorted(report_dir.glob("*-lint.json"))
    if latest:
        print(f"report written  : {latest[-1]}")
    print(f"last_lint_at    : {manager._manifest.get('last_lint_at')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
