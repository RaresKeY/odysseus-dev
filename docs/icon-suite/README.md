# Odysseus UI icon suite

802 editable monochrome SVGs in 17 families, with a searchable review gallery and component color trials for 19 Odysseus themes. Each icon uses a 24-unit viewBox, 2-unit strokes, round caps and joins, transparent backgrounds and `currentColor`. The set has 677 distinct geometry bodies and 125 intentional aliases or shared compositions.

From the repository root, run `python3 -m http.server 8785 --bind 127.0.0.1`, then open [the gallery](http://127.0.0.1:8785/docs/icon-suite/). Serve the repository root so SVG download links resolve. The gallery includes its sprite inline and needs no JavaScript package installation.

- [Full inventory](ICON_INVENTORY.md), [CSV](catalog.csv) and [JSON](catalog.json).
- [Standalone SVG assets](../../static/icons/ui/) and [symbol sprite](../../static/icons/ui/sprite.svg).
- [Theme contract](theme-contract.json), [exact component CSS](upstream-components.css) and [theme screenshot](checks/theme-trials.png).
- [Desktop gallery](checks/gallery-desktop.png) and [narrow gallery](checks/gallery-narrow.png).
- [Imagegen references and exact prompts](references/manifest.json).
- [Fork theme comparison](fork-theme-check.json) confirms the 19 palettes and 13 selected CSS rules also match the fork integration base.
- [Authoring and validation tools](../../tools/icon-suite/README.md).

## Use

Inline SVG or sprite instances inherit their control's color:

```html
<button style="color:var(--fg)" aria-label="Search">
  <svg width="20" height="20" aria-hidden="true">
    <use href="/static/icons/ui/sprite.svg#ody-search"></use>
  </svg>
</button>
```

An SVG loaded with `<img>` has its own document and does not inherit the page's `currentColor`. Provider glyphs are neutral identifiers rather than official logos. The seven imagegen boards supplied family silhouettes and motifs; final vector geometry, variants and aliases were manually authored. No raster, font, script or external dependency is embedded in an icon.

## Coverage and theme provenance

The inventory is pinned to [upstream dev at c21980f5a6d1cb84db8c8ab0f488cefc7764eb3f](https://github.com/odysseus-dev/odysseus/tree/c21980f5a6d1cb84db8c8ab0f488cefc7764eb3f), dated November 3, 2026. It retains 1,648 button and 1,630 SVG occurrences from 718 first-party UI-related source files. Coverage separates 351 existing SVG concepts, 119 candidate control gaps, 281 supporting glyphs and 51 proposed expansions. Text-only controls can be intentional; source mappings need contextual review before replacement. The scan is exhaustive within its documented source bounds, while future UI entries are proposals.

The gallery reproduces the pinned presets and selected component rules: foreground inheritance, rail opacity and active background, accent fallbacks, white Send ink, mixed mic backgrounds and advanced brand/Send overrides. The `ocean` preset is displayed as Starfield. A fixed blue `--color-accent` and the absence of a global `--accent` definition are preserved.

This contribution adds reusable assets and a review gallery. Existing application call sites are unchanged. The authenticated application has not been tested with icon replacements. Screenshots show the running component gallery, not an authenticated Odysseus session. Retained source and CSS excerpts originate from this repository and remain subject to its existing license; no third-party icon package is bundled.
