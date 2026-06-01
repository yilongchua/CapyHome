#!/usr/bin/env python
"""Show the exact judge prompt sent per batch — read-only, no LLM call.

Reconstructs _build_judge_prompt() over real vault candidate pages exactly as
lint_and_prune_pages() does, so you can see what each judging LLM call contains
without disturbing a running judge.

Usage (from backend/):  PYTHONPATH=. .venv/bin/python scripts/show_judge_prompt.py [--n 3]
"""
from __future__ import annotations

import argparse

from src.control_plane.vault_learning import VaultLearningManager
from src.control_plane.vault_text_utils import parse_frontmatter, slugify


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3, help="candidate pages in the sample batch")
    args = ap.parse_args()

    m = VaultLearningManager(vault_root=VaultLearningManager.default_vault_root())
    sources = m._manifest.get("sources", {}) or {}
    live_ids = {str(s) for s in sources if str(s).strip()}

    # Rebuild slug -> source-titles map exactly like lint_and_prune_pages.
    entity_titles: dict[str, list[str]] = {}
    entity_labels: dict[str, str] = {}
    for sid, rec in sources.items():
        if not isinstance(rec, dict):
            continue
        title = str(rec.get("title") or sid).strip() or str(sid)
        for raw in rec.get("entity_refs") or []:
            label = str(raw).strip()
            slug = slugify(label)
            if not slug:
                continue
            if len(label) > len(entity_labels.get(slug, "")):
                entity_labels[slug] = label
            entity_titles.setdefault(slug, []).append(title)

    # Collect the first N non-orphan candidates (the LLM-judged set).
    batch: list[dict] = []
    for path in sorted(m.compiled_entities_dir.glob("*.md")):
        if path.name == "index.md":
            continue
        fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        refs = [str(r) for r in (fm.get("source_refs") or []) if str(r).strip()]
        live = [r for r in refs if r in live_ids]
        if not live:
            continue  # orphan -> auto-flagged, never sent to the LLM
        slug = path.stem
        batch.append({
            "slug": slug,
            "kind": "entity",
            "label": entity_labels.get(slug, slug.replace("-", " ").title()),
            "live_source_count": len(live),
            "is_stub": m._is_stub_page_body(body, "entity"),
            "body_excerpt": body.strip()[:200],
            "source_titles": list(dict.fromkeys(entity_titles.get(slug, [])))[:8],
        })
        if len(batch) >= args.n:
            break

    user_context = m._collect_user_context_for_judge()
    vault_context = m._collect_vault_domain_context()
    prompt = m._build_judge_prompt(batch, user_context, vault_context)

    print("=" * 70)
    print(f"EXACT PROMPT for a {len(batch)}-page batch (the real one uses 20):")
    print("=" * 70)
    print(prompt)
    print("=" * 70)
    print(f"prompt length: {len(prompt)} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
