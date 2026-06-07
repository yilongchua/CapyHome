#!/usr/bin/env python3
"""Convert key-feature Markdown articles into Medium-friendly HTML fragments."""

from __future__ import annotations

import re
from pathlib import Path

from markdown_it import MarkdownIt


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs" / "key-features"


def rewrite_markdown_links(src: str) -> str:
    """Point local Markdown links at the generated sibling HTML files."""
    return re.sub(r"(\]\([^)\n#?]+?)\.md((?:#[^)]+)?\))", r"\1.html\2", src)


def mediumize_figures(html: str) -> str:
    """Turn simple image paragraphs into figures with captions."""
    image_paragraph = re.compile(
        r"<p><img src=\"(?P<src>[^\"]+)\" alt=\"(?P<alt>[^\"]*)\" /></p>",
        re.MULTILINE,
    )

    def replace(match: re.Match[str]) -> str:
        src = match.group("src")
        alt = match.group("alt")
        caption = f"\n  <figcaption>{alt}</figcaption>" if alt else ""
        return f'<figure>\n  <img src="{src}" alt="{alt}" />{caption}\n</figure>'

    return image_paragraph.sub(replace, html)


def render_article(path: Path, md: MarkdownIt) -> str:
    source = rewrite_markdown_links(path.read_text(encoding="utf-8"))
    html = md.render(source).strip()
    html = mediumize_figures(html)
    return f"{html}\n"


def main() -> None:
    md = MarkdownIt("gfm-like", {"html": True, "linkify": False})
    markdown_files = sorted(DOCS_DIR.rglob("*.md"))

    for path in markdown_files:
        output_path = path.with_suffix(".html")
        output_path.write_text(render_article(path, md), encoding="utf-8")
        print(output_path.relative_to(ROOT))


if __name__ == "__main__":
    main()
