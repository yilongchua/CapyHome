# Diagrams

Pre-rendered PNGs for the key-feature article series, in the warm "friendly-tech"
capybara palette (cream background, honey/sage/tan nodes, soft shadows, rounded corners).

- **`*.png`** — the rendered diagrams the articles embed (`<article>-d<N>.png`), at 3× scale.
- **`src/*.mmd`** — the Mermaid source for each diagram (the single source of truth).
- **`_build/`** — the render toolchain:
  - `render.py` — extracts every ```mermaid block from the articles, renders it, and rewrites the block as an image link. (Already run; re-run only to regenerate.)
  - `mermaid-config.json` — theme + palette (themeVariables).
  - `diagram.css` — rounding, drop shadows, and the warm mindmap/flowchart overrides.
  - `puppeteer-config.json` — points mermaid-cli at the system Google Chrome.

## Regenerate everything

```bash
cd docs/key-features
python3 diagrams/_build/render.py
```

This re-renders all diagrams from `src/*.mmd` using the theme files and updates the
image links in the articles. It uses `npx @mermaid-js/mermaid-cli` (downloaded on first
run) driving the installed Google Chrome — no separate Chromium download.

## Render a single diagram

```bash
cd docs/key-features/diagrams/_build
PUPPETEER_SKIP_DOWNLOAD=true npx -y -p @mermaid-js/mermaid-cli mmdc \
  -i ../src/01-knowledge-vault-d1.mmd -o ../01-knowledge-vault-d1.png \
  -c mermaid-config.json -C diagram.css -p puppeteer-config.json -b "#FBF6EC" -s 3
```

## Tweaking the look

Edit `mermaid-config.json` (colors/fonts/spacing) or `diagram.css` (shadows/rounding),
then re-run `render.py`. **Don't** set `font-weight` in the CSS — Mermaid measures node
widths with the base font during layout, so bolding afterwards makes text overflow and clip.

## Palette

| Role | Hex |
|------|-----|
| Background (cream) | `#FBF6EC` |
| Node fill (honey-cream) | `#FDEFD2` |
| Node fill (sage) | `#DCE8D0` |
| Node fill (honey) | `#FBE9C2` / `#E8B04B` |
| Border / lines (brown) | `#8B5E34` / `#C9A66B` |
| Text (ink) | `#3A2E22` |
