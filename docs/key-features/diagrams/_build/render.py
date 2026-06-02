#!/usr/bin/env python3
"""Render every ```mermaid block in the key-features articles to a warm
friendly-tech PNG, and replace the block with an image link.

- PNGs  -> docs/key-features/diagrams/<article>-d<N>.png
- source -> docs/key-features/diagrams/src/<article>-d<N>.mmd  (regenerable)
- alt text is taken from the nearest preceding "### Diagram N — Title" heading

Re-run any time to regenerate from the .mmd sources + theme files.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path("/Users/yi.long.chua/Desktop/CapyHome/docs/key-features")
BUILD = ROOT / "diagrams" / "_build"
SRC = ROOT / "diagrams" / "src"
OUT = ROOT / "diagrams"
CONFIG, CSS, PUP = BUILD / "mermaid-config.json", BUILD / "diagram.css", BUILD / "puppeteer-config.json"

SRC.mkdir(parents=True, exist_ok=True)
ENV = dict(os.environ, PUPPETEER_SKIP_DOWNLOAD="true", PUPPETEER_SKIP_CHROMIUM_DOWNLOAD="true")
BLOCK = re.compile(r"```mermaid\n(.*?)\n```", re.DOTALL)


def alt_for(pos: int, text: str) -> str:
    head = text.rfind("\n### ", 0, pos)
    if head == -1:
        return "CapyHome flow diagram"
    line = text[head + 1: text.find("\n", head + 1)]
    label = line.lstrip("# ").strip()
    label = re.sub(r"^Diagram\s*\d+\s*[—\-]\s*", "", label)
    return label or "CapyHome flow diagram"


def render(mmd: Path, png: Path) -> bool:
    cmd = ["npx", "-y", "-p", "@mermaid-js/mermaid-cli", "mmdc",
           "-i", str(mmd), "-o", str(png), "-c", str(CONFIG), "-C", str(CSS),
           "-p", str(PUP), "-b", "#FBF6EC", "-s", "3"]
    r = subprocess.run(cmd, env=ENV, capture_output=True, text=True)
    if not png.exists():
        print(f"  FAIL: {png.name}\n{r.stderr[-400:]}")
        return False
    return True


def main() -> int:
    total = 0
    for md in sorted(ROOT.glob("[0-1][0-9]-*.md")):
        text = md.read_text()
        matches = list(BLOCK.finditer(text))
        if not matches:
            continue
        pieces, last, n = [], 0, 0
        for m in matches:
            n += 1
            name = f"{md.stem}-d{n}"
            (SRC / f"{name}.mmd").write_text(m.group(1) + "\n")
            if not render(SRC / f"{name}.mmd", OUT / f"{name}.png"):
                return 1
            alt = alt_for(m.start(), text)
            pieces.append(text[last:m.start()])
            pieces.append(f"![{alt}](./diagrams/{name}.png)")
            last = m.end()
            total += 1
            print(f"  ok  {name}  ({alt})")
        pieces.append(text[last:])
        md.write_text("".join(pieces))
        print(f"{md.name}: {n} diagram(s)")
    print(f"\nTOTAL: {total} diagrams rendered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
