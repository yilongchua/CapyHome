#!/usr/bin/env python
"""Standalone Knowledge Vault lint-and-prune runner (Path B).

Wraps VaultLearningManager.lint_and_prune_pages() — the flow that can REMOVE
low-quality entity/concept pages. Runs out-of-process (no langgraph, no event
loop to block).

Safety model:
  * Default is a DRY RUN: nothing is mutated, you just see what WOULD be pruned.
  * --apply actually removes (dismiss entities, delete concept files, strip
    concept_refs, rewrite the concept index, update manifest.last_lint_at).
  * Removal modes:
      - heuristic (default): orphan + dismissed + singleton_stub
      - --use-llm: LLM judge scores every non-auto candidate keep/remove
                   (batches of 20; needs a configured model). NO LLM in dry
                   heuristic mode.
  * Cross-process guard: the manager's ingest guard is in-process only, so
    before --apply we probe the gateway's /api/vault/ingest/status and refuse
    if an ingest is running (pass --no-ingest-check to skip).

Usage (from backend/):
    PYTHONPATH=. .venv/bin/python scripts/run_vault_prune.py                 # dry-run preview, heuristic
    PYTHONPATH=. .venv/bin/python scripts/run_vault_prune.py --use-llm       # dry-run preview, LLM-judged
    PYTHONPATH=. .venv/bin/python scripts/run_vault_prune.py --apply         # ACTUALLY prune (heuristic)
    PYTHONPATH=. .venv/bin/python scripts/run_vault_prune.py --apply --use-llm
"""
from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.request
from pathlib import Path

from src.control_plane.vault_learning import VaultLearningManager


def _gateway_ingest_running(base_url: str) -> bool | None:
    """Returns True/False if reachable, None if the gateway can't be probed."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/vault/ingest/status", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    status = str(data.get("status") or data.get("state") or "").lower()
    return status == "running"


def _print_findings(title: str, section: dict) -> None:
    flagged = section.get("flagged", []) or []
    print(f"\n{title}: total_before={section.get('total_before')} "
          f"flagged={len(flagged)} removed={section.get('removed')}")
    for f in flagged[:50]:
        reasons = ",".join(f.get("reasons", []))
        print(f"  - {f.get('slug'):40s} [{reasons}]")
    if len(flagged) > 50:
        print(f"  ... and {len(flagged) - 50} more")


def main() -> int:
    ap = argparse.ArgumentParser(description="Run knowledge-vault lint-and-prune standalone (Path B).")
    ap.add_argument("--apply", action="store_true", help="actually remove (default: dry-run preview)")
    ap.add_argument("--use-llm", action="store_true", help="use the LLM judge for candidate pages")
    ap.add_argument("--workers", type=int, default=1, help="parallel judge workers (1=sequential, capped at 8). All hit the same model endpoint.")
    ap.add_argument("--batch-size", type=int, default=20, help="pages per LLM judge call (smaller = more, faster calls)")
    ap.add_argument("--vault-root", type=str, default=None, help="override vault root path")
    ap.add_argument("--gateway", type=str, default="http://localhost:8001", help="gateway base URL for ingest-status probe")
    ap.add_argument("--no-ingest-check", action="store_true", help="skip the gateway ingest-running probe before --apply")
    args = ap.parse_args()

    dry_run = not args.apply

    if args.use_llm:
        # Surface per-batch judge progress (vault_lint_llm_batch ...).
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if args.apply and not args.no_ingest_check:
        running = _gateway_ingest_running(args.gateway)
        if running is True:
            print("ABORT: gateway reports a vault ingest is RUNNING. "
                  "Pruning now would race with it. Wait for it to finish "
                  "(or pass --no-ingest-check to override).")
            return 2
        elif running is None:
            print(f"warning: could not reach gateway at {args.gateway} to verify "
                  "ingest status — proceeding (gateway may be down).")

    vault_root = Path(args.vault_root) if args.vault_root else VaultLearningManager.default_vault_root()
    manager = VaultLearningManager(vault_root=vault_root)

    print(f"vault_root : {manager.vault_root}")
    print(f"mode       : {'APPLY (will remove)' if args.apply else 'DRY RUN (preview only)'}")
    print(f"judge      : {'LLM' if args.use_llm else 'heuristic'}")
    if args.use_llm:
        print(f"workers    : {args.workers}  batch_size: {args.batch_size}")
    print("running lint_and_prune_pages() ...", flush=True)

    t0 = time.perf_counter()
    report = manager.lint_and_prune_pages(
        dry_run=dry_run,
        use_llm=args.use_llm,
        judge_batch_size=args.batch_size,
        judge_workers=args.workers,
    )
    dt = time.perf_counter() - t0

    print(f"\ncompleted in {dt:.2f}s  (dry_run={report.get('dry_run')})")
    print("=" * 60)
    _print_findings("ENTITIES", report.get("entities", {}))
    _print_findings("CONCEPTS", report.get("concepts", {}))
    print("=" * 60)
    if not dry_run:
        latest = sorted(manager.lint_reports_dir.glob("*-prune.json"))
        if latest:
            print(f"report written : {latest[-1]}")
        print(f"last_lint_at   : {manager._manifest.get('last_lint_at')}")
    else:
        print("DRY RUN — nothing was modified. Re-run with --apply to prune the flagged pages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
