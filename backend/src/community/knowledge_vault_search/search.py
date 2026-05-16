"""Compatibility exports for compiled knowledge vault search."""

from src.control_plane.services.unified_vault_search import (
    VALID_CATEGORIES,
    VaultSearcher,
    _bm25_score,
    _excerpt,
    _tokenize,
)

__all__ = ["VALID_CATEGORIES", "VaultSearcher", "_bm25_score", "_excerpt", "_tokenize"]
