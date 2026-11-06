# Icon suite authoring

`design/icon-plan.json` owns semantic IDs and families; `glyphs.py` owns editable geometry, deliberate aliases and badge compositions. `build.py` generates `static/icons/ui/`, the catalogs and the gallery in `docs/icon-suite/`. Reference boards and exact prompts are retained alongside the gallery. There is no placeholder fallback for missing geometry.

Validate a fresh checkout with Python alone: `python3 tools/icon-suite/check.py`. This checks the portable SVG export, sprite, manifests, reference hashes and retained audit maps.

To regenerate and verify the full authoring outputs, run from the repository root:

```bash
python3 tools/icon-suite/capture_source.py --repo . --ref c21980f5a6d1cb84db8c8ab0f488cefc7764eb3f
python3 tools/icon-suite/build.py
python3 tools/icon-suite/render.py
python3 tools/icon-suite/check.py --full
```

The explicit source commit must exist in the local Git object database. Source capture reads Git blobs without checking out or executing application code. Captured source copies, 24/96 px PNG previews, family/theme sheets and machine-specific JSON receipts are generated outputs ignored by Git. The compact occurrence audit and candidate maps are retained for review. Rendering needs existing Python, Pillow and `rsvg-convert`; no package installation is performed by these scripts.

`check.py --full` additionally verifies source hashes, generated previews and a passing browser receipt. After the render, run the browser check before the full check. The browser tools use existing Chromium and Deno with a disposable profile and no package dependencies. Serve the repository root on loopback port 8785. On a workstation with the existing headless Gamescope wrapper, run `python3 tools/icon-suite/run_browser_checks.py` inside that wrapper. The wrapper supplies an offscreen Wayland display; the script itself does not create one.

Bounds checking renders a padded viewBox so overflow cannot be hidden by clipping. The browser check verifies search/filter/selection, keyboard use, custom recoloring, all 19 component color trials, generated reference loads and narrow layout. These checks cover the asset gallery and theme contracts. They do not establish full application integration.

## Exported choice contract

Run `python3 tools/icon-suite/check_choices.py` to verify the selected-only application markers, retained model controls and dropdown carets, numeric signs, labels, four-column export layout, sidebar state, Notes served/authored equality and cache version. [The integration review](../../docs/icon-suite/integration/README.md) records the exact export, contextual target corrections, actual app screenshots and conditional controls. This focused static check does not execute the app.

Run `python3 tools/icon-suite/check_visual_geometry.py` with the existing rendering dependencies to verify union, subtraction, intersection and exclusion by sampling their rendered result regions. [Visual-fix comparisons](../../docs/icon-suite/integration/visual-fixes/index.html) retain the previous applied glyphs and final actual-interface crops.
