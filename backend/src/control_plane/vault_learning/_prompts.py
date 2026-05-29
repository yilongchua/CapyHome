"""LLM prompts used by the vault ingest pipeline.

Kept here (rather than under a sibling ``prompts/`` package) because they are
exclusively consumed by ``AnalysisMixin._analyze_source`` and
``AnalysisMixin._generate_source_sections``. Co-locating prompts with their
single caller makes the contract easier to read and edit.
"""

ANALYZE_SOURCE_PROMPT = """You are analyzing a source for a local knowledge vault.

Return strict JSON with these keys:
- summary: string (2-4 sentences)
- key_claims: string[] (5-8 evidence-oriented claims from the source)
- entities: string[] (proper nouns the source is *about* — see rules below)
- concepts: string[] (recurring ideas, frameworks, or themes — see rules below)
- topic_tags: string[] (kebab-case, 1-5 tags)
- open_questions: string[] (gaps the source itself acknowledges)
- gap_queries: string[] (web-search queries that would fill those gaps)
- synthesis_refs: string[] (kebab-case slugs for cross-source synthesis pages)

ENTITY RULES (be strict — bad entities pollute the knowledge graph):
- Include only proper-noun-like items the source is *substantively about*: named people,
  organizations, products, places, named techniques, branded items, specific named events,
  measurable artifacts (e.g. "Black Tourmaline", "Reiki", "Singapore", "Yoga Sutras").
- DO NOT include pronouns, determiners, generic adjectives, or generic nouns. Reject:
  "your", "their", "this", "these", "best", "good", "ancient", "modern", "new", "use",
  "guide", "tips", "ways", "things", "people", "kind", "type", "post", "article", "blog".
- DO NOT include words extracted just because they were capitalized in the title.
- DO NOT include single dictionary words that aren't specific names. "crystals" is a
  concept, not an entity; "Black Tourmaline" is an entity.
- Prefer multi-word proper nouns ("Spiritual Gemmologist Blog") over single bare words.
- If unsure whether something is an entity, put it under "concepts" instead, or omit it.
- Minimum 4 characters; must contain at least one vowel; must not be a stopword.
- Return at most 8 entities. Quality over quantity — fewer is better.

CONCEPT RULES:
- Recurring ideas the source explores ("grounding", "trust score", "crystal healing").
- Avoid raw single English words that are obvious filler.
- Return at most 8 concepts.

GENERAL RULES:
- Be domain-agnostic. Stay grounded in what the text actually says.
- Keep claims concise and evidence-oriented.
- Use kebab-case for topic_tags and synthesis_refs.
- Return JSON only — no preamble, no code fences.

Source title: {title}
Source url: {url}
Topic hint: {topic}

Source text:
{content}
"""

GENERATE_PAGE_PROMPT = """You are compiling a source into an Obsidian-compatible knowledge vault page.

Return strict JSON with these keys:
- summary_markdown: string
- claims_markdown: string
- evidence_markdown: string
- backlink_lines: string[]
- review_items: string[]

Rules:
- Be faithful to the provided analysis.
- Keep the source page concise and scannable.
- Use markdown only inside string values.
- backlink_lines should be ready to place under a Backlinks section.
- Return JSON only.

Source title: {title}
Source url: {url}
Topic hint: {topic}

Analysis JSON:
{analysis_json}

Source text:
{content}
"""
