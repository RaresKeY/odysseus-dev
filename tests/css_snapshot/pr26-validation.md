# Icon label alignment snapshot

The icon integration adds `ody-ui-with-label` to Add Skill, Add User and the chat context pill. The baseline update records these intended changes across all 24 viewport, theme and density variants:

- Add Skill and Add User: block layout becomes flex layout, with centered alignment and justification and a 6 px row/column gap.
- Chat context pill: justification changes from normal to center.

No other inventory element or property changed. The login and selector-bench snapshots remain identical.

Validation compared fork dev `9ed1fdcb` with icon head `e6a5aa97` using Chromium over its private debugging pipe, the committed `pageMeasure` function, inventory and Python summarization. The dev capture reproduced the committed baseline exactly before the icon capture was accepted. Browser input capabilities, script blocking, resource blocking, font preference and scrollbars matched the normal harness. The complete capture measured all three pages and 24 variants, with no missing elements.

This capture does not run application JavaScript. Authenticated application checks and interface screenshots are documented separately in `docs/icon-suite/integration/`.
