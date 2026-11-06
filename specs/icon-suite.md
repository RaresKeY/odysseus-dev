# UI icon suite

Last updated: dev@c21980f | 2026-11-06

## Scope

`static/icons/ui/` contains 802 standalone SVGs and `sprite.svg`. `tools/icon-suite/design/icon-plan.json` owns semantic IDs and families; `tools/icon-suite/glyphs.py` owns editable geometry, aliases and badge compositions. `build.py` generates the assets, catalogs, occurrence maps and review gallery in `docs/icon-suite/`.

## Asset and gallery contract

Each glyph uses a 24-unit viewBox, 2-unit strokes, round caps and joins, a transparent background and `currentColor`. Standalone SVG titles name their semantic action. Sprite instances are decorative inside controls whose accessible name is owned by the caller. Inline SVG and sprite uses inherit control color; SVG documents loaded through `img` do not inherit the outer page color. No raster, font, script or external resource is embedded in a glyph.

The set has 677 distinct geometry bodies and 125 deliberate shared aliases or compositions across 17 families. Seven generated image references and exact prompts establish family motifs; final vectors are manually authored. Provider glyphs are neutral identifiers rather than official vendor logos.

Catalog metadata is encoded into fixed-origin URL path components, and copied SVGs use DOM construction and XML serialization so labels cannot become markup.

The gallery has search, family/coverage filtering, selection, copy/download, size and custom-color trials, plus independent component trials for 19 themes. Serve the repository root to resolve links from `docs/icon-suite/` to `static/icons/ui/`. The gallery is a standalone review surface; no production control, application route or service-worker precache is changed.

## Source and theme ownership

The inventory is pinned to upstream `c21980f5a6d1cb84db8c8ab0f488cefc7764eb3f`. It retains 1,648 button and 1,630 SVG occurrences from 718 scanned source files. Coverage distinguishes existing concepts, candidate gaps, supporting glyphs and proposed expansions. Label mappings are review candidates rather than an automatic replacement patch.

`theme-contract.json` retains exact palettes and selected component rules from the pin. `fork-theme-check.json` records their equality to all 19 palettes and 13 selected rules on fork base `85469fa38289781ff1d2fd84e56c63f82ed0ffae`. Rail opacity, accent fallback, fixed active blue, white Send ink, mixed mic background and advanced overrides remain source-derived.

## Validation

`python3 tools/icon-suite/check.py` validates the portable export with the Python standard library. `--full` also checks regenerated 24/96 px previews, padded geometry bounds, source hashes and a browser receipt. Rendering needs existing Pillow and librsvg. Browser checks use existing Chromium and Deno without package dependencies; they need an offscreen Wayland display and a loopback server on port 8785. Captured source copies, bulk renders and machine-specific receipts are ignored; selected browser screenshots and the compact validation summary are retained.

## Current gaps

Full authenticated application integration has not been run. Existing UI call sites still use their current icons. Text-only controls can be intentional, expanded affordances are proposals, and per-control replacements need contextual review.
