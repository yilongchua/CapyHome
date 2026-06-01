#!/usr/bin/env python
"""Tiny end-to-end check of the vault LLM judge after the parser fix.

Grabs a handful of real entity pages, runs them through
VaultLearningManager._judge_pages_with_llm(), and prints the parsed verdicts.
Read-only: it never mutates the vault. Use it to confirm the judge produces
parseable keep/remove verdicts (previously it silently parsed 0).

Usage (from backend/):
    PYTHONPATH=. .venv/bin/python scripts/verify_llm_judge.py [--n 8]
"""
from __future__ import annotations

import argparse
import logging
import time

from src.control_plane.vault_learning import VaultLearningManager
from src.control_plane.vault_text_utils import parse_frontmatter


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8, help="how many entity pages to judge")
    ap.add_argument("--workers", type=int, default=1, help="parallel judge workers")
    ap.add_argument("--batch-size", type=int, default=20, help="pages per LLM call")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    manager = VaultLearningManager(vault_root=VaultLearningManager.default_vault_root())
    candidates: list[dict] = []
    for path in sorted(manager.compiled_entities_dir.glob("*.md")):
        if path.name == "index.md":
            continue
        try:
            fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        source_refs = [str(r) for r in (fm.get("source_refs") or []) if str(r).strip()]
        if not source_refs:
            continue  # orphan -> auto-flagged, not an LLM candidate
        candidates.append({
            "slug": path.stem,
            "kind": "entity",
            "label": str(fm.get("label") or path.stem.replace("-", " ").title()),
            "live_source_count": len(source_refs),
            "is_stub": manager._is_stub_page_body(body, "entity"),
            "body_excerpt": body.strip()[:200],
            "source_titles": [],
        })
        if len(candidates) >= args.n:
            break

    print(f"judging {len(candidates)} entity pages:")
    for c in candidates:
        print(f"  - {c['slug']:40s} sources={c['live_source_count']} stub={c['is_stub']}")

    user_context = manager._collect_user_context_for_judge()
    vault_context = manager._collect_vault_domain_context()

    print(f"workers={args.workers} batch_size={args.batch_size}")
    t0 = time.perf_counter()
    verdicts = manager._judge_pages_with_llm(
        candidates, user_context=user_context, vault_context=vault_context,
        batch_size=args.batch_size, max_workers=args.workers,
    )
    dt = time.perf_counter() - t0

    print(f"\nparsed {len(verdicts)} verdicts in {dt:.1f}s")
    print("-" * 60)
    for c in candidates:
        v = verdicts.get(c["slug"])
        if v:
            print(f"  {v['verdict'].upper():6s} {c['slug']:38s} {v['reason']}")
        else:
            print(f"  (none) {c['slug']:38s} <no verdict returned>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
