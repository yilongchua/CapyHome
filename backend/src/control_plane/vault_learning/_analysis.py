from __future__ import annotations

import json
import re
from typing import Any

from src.config import get_app_config
from src.control_plane.vault_learning._prompts import ANALYZE_SOURCE_PROMPT, GENERATE_PAGE_PROMPT
from src.models.factory import create_chat_model


class AnalysisMixin:
    _ENTITY_STOPWORDS: frozenset[str] = frozenset(
        {
            # pronouns / determiners
            "the", "and", "for", "with", "from", "into", "onto", "your", "their", "there", "they",
            "them", "our", "ours", "his", "her", "hers", "its", "this", "that", "these", "those",
            "what", "which", "who", "whom", "whose", "why", "how", "when", "where",
            # generic adjectives / fillers commonly capitalized in titles
            "best", "good", "great", "top", "new", "old", "use", "uses", "using", "ancient",
            "modern", "more", "most", "less", "many", "much", "some", "any", "all", "every",
            "guide", "intro", "overview", "review", "tips", "ways", "list", "blog", "post",
            "article", "page", "site", "home", "next", "back", "here", "now", "soon", "today",
            "yesterday", "tomorrow", "still", "just", "also", "ever", "even", "only", "very",
            "really", "quite", "rather", "such", "than", "then", "still", "yet", "again",
            "etc", "via", "about", "above", "below", "across", "after", "before", "between",
            "during", "without", "within", "behind", "beyond", "under", "over",
            "is", "are", "was", "were", "be", "been", "being",
            # generic nouns that aren't useful as entities
            "thing", "things", "stuff", "people", "person", "way", "ways", "part", "parts",
            "kind", "kinds", "type", "types", "case", "cases", "fact", "facts", "idea", "ideas",
        }
    )

    @staticmethod
    def _extract_json_payload(text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {}
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw = raw[start : end + 1]
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _heuristic_sentences(text: str, *, limit: int) -> list[str]:
        sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", text.strip()) if item.strip()]
        return sentences[: max(1, limit)]

    @classmethod
    def _is_quality_entity(cls, token: str) -> bool:
        cleaned = token.strip(" -_/&")
        if len(cleaned) < 4:
            return False
        lowered = cleaned.lower()
        if lowered in cls._ENTITY_STOPWORDS:
            return False
        # Require at least one vowel (filters acronyms / typos / pure punctuation residue)
        if not re.search(r"[aeiouAEIOU]", cleaned):
            return False
        # Reject if all characters are the same letter or it's purely numeric
        if cleaned.isdigit():
            return False
        return True

    def _heuristic_analysis(
        self,
        *,
        title: str,
        url: str,
        topic: str,
        raw_text: str,
        topic_tags: list[str],
        concept_refs: list[str],
        entity_refs: list[str],
        target_synthesis_refs: list[str],
    ) -> dict[str, Any]:
        summary = " ".join(self._heuristic_sentences(raw_text, limit=3))[:1000]
        key_claims = self._heuristic_sentences(raw_text, limit=5)
        # Prefer multi-word capitalized phrases (proper nouns) over isolated capitalized words,
        # which in titles are usually just adjectives ("Best", "Ancient", "Your").
        multiword = re.findall(r"(?:[A-Z][A-Za-z0-9&/-]{2,}(?:\s+[A-Z][A-Za-z0-9&/-]{2,})+)", title)
        single = re.findall(r"[A-Z][A-Za-z0-9&/-]{3,}", title)
        candidate_tokens = list(dict.fromkeys(multiword + single))
        title_tokens = [token for token in candidate_tokens if self._is_quality_entity(token)]
        topic_words = [item for item in re.findall(r"[A-Za-z0-9]+", topic) if len(item) > 4 and self._is_quality_entity(item)]
        cleaned_entity_refs = [ref for ref in entity_refs if self._is_quality_entity(ref)]
        entities = list(dict.fromkeys(cleaned_entity_refs + title_tokens[:5]))
        concepts = list(dict.fromkeys(concept_refs + topic_words[:6]))
        synthesis_refs = list(dict.fromkeys(target_synthesis_refs + topic_tags[:3] + ([self._topic_slug(topic)] if topic else [])))
        open_questions = [f"What evidence is still missing around {topic or title}?", f"Which facts should be re-verified from {url}?"]
        gap_queries = [f"{topic or title} latest evidence", f"{topic or title} contradictory sources"]
        return {
            "summary": summary or title,
            "key_claims": key_claims or [title],
            "entities": entities,
            "concepts": concepts,
            "topic_tags": topic_tags,
            "open_questions": open_questions,
            "gap_queries": gap_queries,
            "synthesis_refs": [item for item in synthesis_refs if item],
        }

    def _call_vault_model_json(self, prompt: str) -> dict[str, Any]:
        model_name = str(
            getattr(self, "analysis_model_override", None) or self.vault_config.cot_model or ""
        ).strip()
        try:
            app_config = get_app_config()
        except Exception:
            return {}
        if not app_config.models:
            return {}
        model = create_chat_model(name=model_name or None, thinking_enabled=False)
        response = model.invoke(prompt)
        raw = response.content if isinstance(response.content, str) else str(response.content)
        return self._extract_json_payload(raw)

    def _analyze_source(
        self,
        *,
        title: str,
        url: str,
        topic: str,
        raw_text: str,
        topic_tags: list[str],
        concept_refs: list[str],
        entity_refs: list[str],
        target_synthesis_refs: list[str],
    ) -> dict[str, Any]:
        fallback = self._heuristic_analysis(
            title=title,
            url=url,
            topic=topic,
            raw_text=raw_text,
            topic_tags=topic_tags,
            concept_refs=concept_refs,
            entity_refs=entity_refs,
            target_synthesis_refs=target_synthesis_refs,
        )
        if not self.vault_config.cot_ingest_enabled or len(raw_text) < int(self.vault_config.cot_min_chars):
            return {**fallback, "analysis_mode": "heuristic"}
        try:
            parsed = self._call_vault_model_json(
                ANALYZE_SOURCE_PROMPT.format(
                    title=title,
                    url=url,
                    topic=topic,
                    content=raw_text[: self.max_content_chars],
                )
            )
        except Exception:
            parsed = {}
        merged = {
            **fallback,
            **{key: value for key, value in parsed.items() if value not in (None, "", [], {})},
        }
        merged["analysis_mode"] = "model" if parsed else "heuristic"
        for key in ("key_claims", "entities", "concepts", "topic_tags", "open_questions", "gap_queries", "synthesis_refs"):
            value = merged.get(key)
            if not isinstance(value, list):
                merged[key] = fallback.get(key, [])
            else:
                merged[key] = [str(item).strip() for item in value if str(item).strip()]
        merged["entities"] = [item for item in merged["entities"] if self._is_quality_entity(item)]
        merged["summary"] = str(merged.get("summary") or fallback["summary"]).strip()
        return merged

    def _generate_source_sections(
        self,
        *,
        title: str,
        url: str,
        topic: str,
        raw_text: str,
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        fallback = {
            "summary_markdown": str(analysis.get("summary") or title).strip(),
            "claims_markdown": "\n".join(f"- {item}" for item in analysis.get("key_claims", [])[:8]) or f"- {title}",
            "evidence_markdown": "\n".join(f"- {item}" for item in self._heuristic_sentences(raw_text, limit=6)) or raw_text[:1200],
            "backlink_lines": [f"[[../syntheses/{item}.md]]" for item in analysis.get("synthesis_refs", [])[:8]],
            "review_items": [str(item) for item in analysis.get("open_questions", [])[:8]],
        }
        if not self.vault_config.cot_ingest_enabled or len(raw_text) < int(self.vault_config.cot_min_chars):
            return {**fallback, "generation_mode": "heuristic"}
        try:
            parsed = self._call_vault_model_json(
                GENERATE_PAGE_PROMPT.format(
                    title=title,
                    url=url,
                    topic=topic,
                    analysis_json=json.dumps(analysis, ensure_ascii=False, indent=2),
                    content=raw_text[: self.max_content_chars],
                )
            )
        except Exception:
            parsed = {}
        merged = {
            **fallback,
            **{key: value for key, value in parsed.items() if value not in (None, "", [], {})},
        }
        merged["generation_mode"] = "model" if parsed else "heuristic"
        merged["summary_markdown"] = str(merged.get("summary_markdown") or fallback["summary_markdown"]).strip()
        merged["claims_markdown"] = str(merged.get("claims_markdown") or fallback["claims_markdown"]).strip()
        merged["evidence_markdown"] = str(merged.get("evidence_markdown") or fallback["evidence_markdown"]).strip()
        merged["backlink_lines"] = [str(item).strip() for item in merged.get("backlink_lines", []) if str(item).strip()]
        merged["review_items"] = [str(item).strip() for item in merged.get("review_items", []) if str(item).strip()]
        return merged
