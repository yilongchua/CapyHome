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
