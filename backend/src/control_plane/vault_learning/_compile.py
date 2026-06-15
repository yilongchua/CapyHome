from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.control_plane.services.unified_vault_search import UnifiedVaultSearchService
from src.control_plane.vault_text_utils import (
    utcnow as _utcnow,
)
from src.control_plane.vault_text_utils import (
    utcnow_iso as _utcnow_iso,
)


class CompileMixin:
    def _render_index_for_dir(self, title: str, directory: Path) -> str:
        lines = [f"# {title}", ""]
        for path in sorted(directory.glob("*.md")):
            if path.name == "index.md":
                continue
            lines.append(f"- [{path.stem.replace('-', ' ').title()}]({path.name})")
        return "\n".join(lines) + "\n"

    def _render_main_index(self) -> str:
        lines = [
            "# Knowledge Vault Index",
            "",
            f"Updated: {_utcnow_iso()}",
            "",
            "## Compiled Areas",
            "- [Sources](sources/index.md)",
            "- [Concepts](concepts/index.md)",
            "- [Entities](entities/index.md)",
            "- [Syntheses](syntheses/index.md)",
            "- [Queries](queries/index.md)",
            "",
            "## Recent Sources",
        ]
        sources = sorted(
            self._manifest["sources"].values(),
            key=lambda item: str(item.get("last_ingested_at") or ""),
            reverse=True,
        )
        for item in sources[:20]:
            title = str(item.get("title") or item.get("url") or "Untitled")
            path = Path(str(item.get("compiled_path") or ""))
            if path.name:
                lines.append(f"- [{title}](sources/{path.name})")
        return "\n".join(lines) + "\n"

    def _render_log(self, changed_pages: list[str]) -> str:
        lines = [
            "# Knowledge Vault Log",
            "",
            f"Compiled at: {_utcnow_iso()}",
            f"Changed pages: {len(changed_pages)}",
            "",
        ]
        lines.extend(f"- {page}" for page in changed_pages)
        return "\n".join(lines) + "\n"

    def compile_incremental(self) -> dict[str, Any]:
        dirty_pages = list(dict.fromkeys(self._manifest.get("dirty_pages", [])))
        compiled_pages: list[str] = []

        indexes = {
            "02_compiled/index.md": (self.compiled_index_path, self._render_main_index()),
            "02_compiled/log.md": (self.compiled_log_path, self._render_log(dirty_pages or ["bootstrap"])),
            "02_compiled/sources/index.md": (
                self.compiled_sources_dir / "index.md",
                self._render_index_for_dir("Sources", self.compiled_sources_dir),
            ),
            "02_compiled/concepts/index.md": (
                self.compiled_concepts_dir / "index.md",
                self._render_index_for_dir("Concepts", self.compiled_concepts_dir),
            ),
            "02_compiled/entities/index.md": (
                self.compiled_entities_dir / "index.md",
                self._render_index_for_dir("Entities", self.compiled_entities_dir),
            ),
            "02_compiled/syntheses/index.md": (
                self.compiled_syntheses_dir / "index.md",
                self._render_index_for_dir("Syntheses", self.compiled_syntheses_dir),
            ),
            "02_compiled/queries/index.md": (
                self.compiled_queries_dir / "index.md",
                self._render_index_for_dir("Queries", self.compiled_queries_dir),
            ),
        }

        if not dirty_pages:
            dirty_pages = list(indexes.keys())

        for key, (path, content) in indexes.items():
            if key not in dirty_pages and path.exists():
                continue
            path.write_text(content, encoding="utf-8")
            compiled_pages.append(key)
            self._index_document(
                doc_id=key,
                kind="index",
                title=path.stem.replace("-", " ").title(),
                path=path,
                text=content,
                tags=["index"],
            )

        compile_report = {
            "status": "compiled",
            "compiled_count": len(compiled_pages),
            "compiled_pages": compiled_pages,
            "index_path": str(self.compiled_index_path),
            "log_path": str(self.compiled_log_path),
        }
        search_service = UnifiedVaultSearchService(self.vault_root)
        vector_status = search_service.vector_status(build_if_stale=True)
        compile_report["vector_index"] = vector_status
        report_path = self.compile_reports_dir / f"{_utcnow().strftime('%Y%m%dT%H%M%SZ')}-compile.json"
        report_path.write_text(json.dumps(compile_report, indent=2), encoding="utf-8")
        self._manifest["dirty_pages"] = []
        self._manifest["last_compile_at"] = _utcnow_iso()
        self._manifest["last_run_summary"] = {"step": "compile", "updated_at": _utcnow_iso(), **compile_report}
        self._save_manifest()
        return compile_report

    def compile_indexes(self) -> dict[str, Any]:
        return self.compile_incremental()
