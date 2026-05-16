ANALYZE_SOURCE_PROMPT = """You are analyzing a source for a local knowledge vault.

Return strict JSON with these keys:
- summary: string
- key_claims: string[]
- entities: string[]
- concepts: string[]
- topic_tags: string[]
- open_questions: string[]
- gap_queries: string[]
- synthesis_refs: string[]

Rules:
- Be domain-agnostic.
- Prefer concrete entities and concepts actually supported by the source.
- Keep claims concise and evidence-oriented.
- Use kebab-case for topic_tags and synthesis_refs.
- Return JSON only.

Source title: {title}
Source url: {url}
Topic hint: {topic}

Source text:
{content}
"""
