# Selected icon interface review

Applied 116 of the 118 selected new icon concepts to the fork PR. The other 140 explicit choices and 544 unspecified IDs keep their old glyphs. Database and Unlink remain two documented semantic guards. Shape Intersect has been redrawn with an actual overlap result and is applied to the selected control and matching Combine Shapes command.

The authenticated, disposable Odysseus UI capture covers 83 states and directly observes 89 applied IDs. 27 applied IDs remain conditional or CSS-hidden and are explicitly marked below. Of 43 choices captured from literal text, 36 were observed. The capture has zero page JavaScript exceptions, zero automation errors and 0 SVG containment failures. Counts refer to semantic IDs and retain the first unobscured instance seen; they do not certify every repeated instance or hover/state variant. A context screenshot does not verify an unobserved glyph.

TEXT meant literal source button content. TYPE MARK covers B/I/U and superscript; MENU LABEL covers named editor commands; TEXT LABEL covers command names such as Harmonize; FORMAT LABEL covers PNG/JPEG or mode names; STEPPER covers numeric plus/minus. CARET is a separate SVG role used for dropdowns and expandable panels. The gallery now distinguishes these roles across all 620 compared source variants.

Compound controls need both their semantic mark and their disclosure affordance. Model pickers, font selectors, heading selectors and serving-engine filters should reuse the same caret pattern. Their action icon must not consume the arrow. Existing model picker and refresh markup is unchanged; heading/font/engine/reasoning carets are retained.

Changed text controls were checked in the actual document toolbar, transform toolbar, Notes drawing toolbar and export dialog. Formatting SVG boxes remain 13 px, command glyphs generally use 14 px, and numeric field glyphs preserve arithmetic signs. PNG/JPEG labels remain visible at desktop and 390 px. Notes drawing badges and served-module copies are preserved. These checks do not establish every conditional provider workflow.

Recommended follow-up in the fork: rename the mobile document Unlink action to Close; keep Calendar Refresh as a refresh arrow; call the archived-note glyph archive rather than save; rename the captured Admin Test alias to webhook-test rather than skill-test; classify comparison execution-mode icons separately from Deep Research; retain readable command and format labels. Do not remake generic semantic glyphs to replace carets or dormant text-menubar fragments. Revisit the smallest composite badge icons at 12–14 px only after a real owner screenshot shows they are hard to read.

The fork was rebased onto newer Odysseus code during this work. Final after screenshots and source hashes use fork base 907efde0 plus the visual fixes. Historical before screenshots retain the original e71b14fc baseline; the dedicated visual-fix page compares previously applied glyphs and the corrected small-size candidates. All 174 focused checks pass on the rebased code; seven document tests that invoke Node were deselected.

The browser ran inside the secretless Pi Dev Workbench runner with network disabled, a temporary admin account and disposable data. App startup used lifespan off and MCP disabled. The chat tool events are explicit rendering fixtures. A supplementary photo/Calendar probe timed out and supplies no passing evidence. No external model, mail, TTS, vault, model download or ML operation was tested. The actual app capture used software compositing; the separate gallery's 40 checks used the workstation GPU. The full backend test suite and physical touchscreen behavior were not run.

Earlier capture attempts exposed an automation-only Settings lookup error and a served-Notes-copy mismatch. The automation was corrected to import the existing Settings owner; an unsupported /settings route was also rejected from the evidence; the Notes copies were synchronized before the successful capture. The latest visual fixes remove a duplicate Text color letter and underline, center the Heading mark, simplify Path anchors and Gradient bands, and redraw the four boolean operations. The cache numeric version is v1140. The original overlap regression remains intact and passes with the redrawn intersection glyph. Superseded failed attempts are not used as passing evidence.

[Open the captioned screenshot report](review.html). [Every selected ID and visual status](visual-coverage.csv). [Exact choices](choices.json). [Applied targets and contextual remaps](application-manifest.json). [All source control classifications](control-classification.json).

## Before and after

### Document formatting

B, I, U, strikethrough, superscript and subscript were intentional typographic button marks. Their selected SVGs use 13 px boxes. The heading H and font Aa controls keep their separate 8 px dropdown carets. The toolbar stays on its existing baseline.

[Before](screenshots/before/03-document-editing.png) · [After](screenshots/after/03-document-editing.png)

### Heading dropdown

The inventory exported only a fragment of this compound control. H identifies the formatting action; the adjacent chevron discloses the menu. A generic Heading icon must not replace that chevron.

[Before](screenshots/before/04-document-heading.png) · [After](screenshots/after/04-document-heading.png)

### Font dropdown

The font mark and the menu arrow have different jobs. The new semantic mark replaces Aa; the small caret is reused. Menu labels remain readable text.

[Before](screenshots/before/05-document-font.png) · [After](screenshots/after/05-document-font.png)

### Numeric transform controls

Width and height are fields, and their plus/minus controls are numeric steppers. The chosen width, height, skew and perspective glyphs now sit beside the field labels. Increment/decrement buttons retain + and minus; the fields fit the existing toolbar.

[Before](screenshots/before/editor-tool-transform.png) · [After](screenshots/after/editor-tool-transform.png)

### Export format labels

PNG and JPEG are format names, not missing icons. New glyphs accompany their text. The original four formats occupied a three-column grid; four equal columns now keep PNG, JPEG, WebP and PSD on one row.

[Before](screenshots/before/editor-export.png) · [After](screenshots/after/editor-export.png)

### Export at 390 px

The same four format buttons fit the narrow dialog. SVGs and labels remain inside their buttons. This verifies the export dialog at a mobile viewport; it does not establish every editor touch workflow.

[Before](screenshots/before/editor-export-narrow.png) · [After](screenshots/after/editor-export-narrow.png)

### Notes drawing toolbar

The original T and diagonal line were drawing controls. The selected editable glyphs replace those marks while preserving the text/line size badges and the existing event hooks. Both authored and actually served Notes modules are synchronized.

[Before](screenshots/before/notes-drawing.png) · [After](screenshots/after/notes-drawing.png)

### Model picker and disclosure arrows

Select model opens a list; its arrow is a structural disclosure affordance. Reuse that caret rather than a model cube or another semantic icon. The upward caret is appropriate because the menu opens above the composer. The picker owner sets aria-haspopup and updates aria-expanded. The model button and refresh action are retained. This account has no model endpoints.

[Before](screenshots/before/02-model-picker.png) · [After](screenshots/after/02-model-picker.png)

### Sidebar expanded

The active external toggle now shows the selected close-sidebar glyph with Hide sidebar as its accessible label. Notes and Theme use the selected glyph in both the expanded sidebar and the collapsed rail.

[Before](screenshots/before/01-sidebar-expanded.png) · [After](screenshots/after/01-sidebar-expanded.png)

### Sidebar collapsed

The toggle returns to the old hamburger and Show sidebar label. A close icon is inappropriate for an action that opens the sidebar. The captured in-sidebar toggle was dormant CSS-hidden markup.

[Before](screenshots/before/01-sidebar-collapsed.png) · [After](screenshots/after/01-sidebar-collapsed.png)

### Keyboard Shortcuts

The selected keyboard glyph is placed on the visible Help menu command. The captured secondary header shortcut glyph was hidden by the current menubar layout.

[Before](screenshots/before/editor-menu-help.png) · [After](screenshots/after/editor-menu-help.png)

### Visible export command and hidden File fragment

Export Image now carries the selected glyph and retains its label. The File menubar glyph itself remains hidden by existing CSS; its visible text label is intentional.

[Before](screenshots/before/editor-menu-file.png) · [After](screenshots/after/editor-menu-file.png)

### Tool status glyphs in chat

Memory Search and Image Generate are rendered by the real chat renderer from explicit local fixture tool events. No model request, memory tool execution or image service was invoked.

[Before](screenshots/before/chat-tool-fixture.png) · [After](screenshots/after/chat-tool-fixture.png)

### Shape Intersect: rejected earlier candidate

Historical rejected candidate: the first Shape Intersect SVG used a rectangle/check badge. It has now been replaced by two operand circles with the intersection result emphasized. The current per-ID screenshot below shows the corrected glyph; the original intersect regression test remains intact.

[Rejected candidate in the actual interface](screenshots/rejected/shape-intersect.png)

## All selected new choices

| ID | Visual status | Evidence and explanation |
| --- | --- | --- |
| notes | observed | [Actual icon](screenshots/after/notes.png). Visible, unobscured in 01-shell. SVG is 14 by 14 px; contained in its owner control. |
| theme | observed | [Actual icon](screenshots/after/theme.png). Visible, unobscured in 01-shell. SVG is 14 by 14 px; contained in its owner control. |
| add | observed | [Actual icon](screenshots/after/add.png). Visible, unobscured in editor-tool-gradient. SVG is 14 by 14 px; contained in its owner control. |
| rename | observed | [Actual icon](screenshots/after/rename.png). Visible, unobscured in settings-users. SVG is 14 by 14 px; contained in its owner control. |
| delete | observed | [Actual icon](screenshots/after/delete.png). Visible, unobscured in notes-saved-checklist. SVG is 10 by 10 px; contained in its owner control. |
| download | observed | [Actual icon](screenshots/after/download.png). Visible, unobscured in feature-cookbook. SVG is 14 by 14 px; contained in its owner control. |
| save | not-observed | [Context only](screenshots/after/notes-saved-actions.png). The selected Save (archive) SVG is in a legacy Notes action footer hidden by the current card layout. The visible card uses Mark done. Keep archiving terminology; rename this catalog concept archive before wider reuse. |
| deselect | observed | [Actual icon](screenshots/after/deselect.png). Visible, unobscured in notes-bulk-selection. SVG is 12 by 12 px; contained in its owner control. |
| filter | not-observed | [Context only](screenshots/after/02-model-picker.png). Serving-engine filter is conditional on the hardware-fit results view. Its separate disclosure caret is preserved. No hardware scan or provider download was invoked. Screenshot shows the fixture model-picker context, not this unobserved glyph. |
| filter-clear | not-observed | [Context only](screenshots/after/02-model-picker.png). The Clear all filter action was not observed in the fixture. It retains its text label; no blanket replacement of gallery clear buttons was made. Screenshot shows the fixture model-picker context, not this unobserved glyph. |
| sort | not-observed | [Context only](screenshots/after/01-sidebar-expanded.png). Session Sort needs a populated session list. The empty disposable account did not expose this control. |
| favorite | not-observed | [Context only](screenshots/after/02-model-picker.png). The real photo detail header was not exposed by the fixture. Dynamic favorite paint is retained in source, but this control remains unverified. Screenshot shows the fixture model-picker context, not this unobserved glyph. |
| unlink | kept-old | [Context only](screenshots/after/03-document-editing.png). Captured mobile Unlink button closes the document tab. Keep it for now; fix the misleading label separately instead of adding a broken-link symbol. |
| open | not-observed | [Context only](screenshots/after/02-model-picker.png). Open in Launch requires a downloaded, launchable model task. No model download or launch was performed. Screenshot shows the fixture model-picker context, not this unobserved glyph. |
| move | observed | [Actual icon](screenshots/after/move.png). Visible, unobscured in 06-editor. SVG is 18 by 18 px; contained in its owner control. |
| clear | not-observed | [Context only](screenshots/after/01-sidebar-expanded.png). Clear selected Notes/Todos context appears only after attaching context to chat. This state was not exercised. |
| expand | not-observed | [Context only](screenshots/after/02-model-picker.png). Comparison pane expand is conditional on an active comparison. No provider-backed comparison was run. |
| collapse | not-observed | [Context only](screenshots/after/02-model-picker.png). Comparison pane collapse is conditional on an expanded comparison pane. The Settings navigation chevron was retained. |
| maximize | observed | [Actual icon](screenshots/after/maximize.png). Visible, unobscured in 06-editor. SVG is 14 by 14 px; contained in its owner control. |
| panel-right | not-observed | [Context only](screenshots/after/editor-menu-window.png). Window panels has a semantic glyph and separate caret inside ge-stacked-glyph, hidden by the current text-menubar CSS. Keep the text label and caret; do not force this dormant glyph into the menubar. |
| sidebar-close | observed | [Actual icon](screenshots/after/sidebar-close.png). Visible, unobscured in 01-shell. SVG is 18 by 18 px; contained in its owner control. |
| dock | not-observed | [Context only](screenshots/after/editor-history.png). Dock panel belongs to the editor floating-panel UI. No floating dock panel was created; the History popup is a different component. |
| quote | not-observed | [Context only](screenshots/after/02-model-picker.png). Quoted email-thread fold needs a loaded email thread. No mailbox was configured or contacted. Screenshot shows the fixture model-picker context, not this unobserved glyph. |
| reasoning | not-observed | [Context only](screenshots/after/02-model-picker.png). Reasoning controls depend on model capabilities. No reasoning-capable provider was configured. Source check confirms the new semantic mark is alongside the retained dropdown caret. |
| context | not-observed | [Context only](screenshots/after/02-model-picker.png). Chat context meter depends on an active session/model context. The disposable account did not expose it. The AI Draft reply button was retained. |
| model-add | observed | [Actual icon](screenshots/after/model-add.png). Visible, unobscured in 02-model-picker. SVG is 14 by 14 px; contained in its owner control. |
| database | kept-old | [Context only](screenshots/after/02-model-picker.png). Captured source refreshes calendar data. Keep the refresh arrow; a database cylinder does not convey refresh. Screenshot shows the fixture model-picker context, not this unobserved glyph. |
| file | not-observed | [Context only](screenshots/after/editor-menu-file.png). The File semantic glyph is inside ge-stacked-glyph, which the current text-menubar CSS hides. The visible File label remains correct. Avoid duplicating a file icon just to replace a hidden fragment. |
| bold | observed | [Actual icon](screenshots/after/bold.png). Visible, unobscured in 03-document. SVG is 13 by 13 px; contained in its owner control. |
| italic | observed | [Actual icon](screenshots/after/italic.png). Visible, unobscured in 03-document. SVG is 13 by 13 px; contained in its owner control. |
| underline | observed | [Actual icon](screenshots/after/underline.png). Visible, unobscured in 03-document. SVG is 13 by 13 px; contained in its owner control. |
| strikethrough | observed | [Actual icon](screenshots/after/strikethrough.png). Visible, unobscured in 03-document. SVG is 13 by 13 px; contained in its owner control. |
| superscript | observed | [Actual icon](screenshots/after/superscript.png). Visible, unobscured in 03-document-editing. SVG is 13 by 13 px; contained in its owner control. |
| subscript | observed | [Actual icon](screenshots/after/subscript.png). Visible, unobscured in 03-document-editing. SVG is 13 by 13 px; contained in its owner control. |
| heading | observed | [Actual icon](screenshots/after/heading.png). Visible, unobscured in 03-document. SVG is 13 by 13 px; contained in its owner control. |
| font-family | observed | [Actual icon](screenshots/after/font-family.png). Visible, unobscured in 03-document. SVG is 13 by 13 px; contained in its owner control. |
| font-size | observed | [Actual icon](screenshots/after/font-size.png). Visible, unobscured in document-email. SVG is 12 by 12 px; contained in its owner control. |
| text-color | observed | [Actual icon](screenshots/after/text-color.png). Visible, unobscured in 03-document. SVG is 13 by 13 px; contained in its owner control. |
| highlight | observed | [Actual icon](screenshots/after/highlight.png). Visible, unobscured in 03-document. SVG is 13 by 13 px; contained in its owner control. |
| indent | observed | [Actual icon](screenshots/after/indent.png). Visible, unobscured in 03-document-editing. SVG is 13 by 13 px; contained in its owner control. |
| line-spacing | observed | [Actual icon](screenshots/after/line-spacing.png). Visible, unobscured in 03-document-editing. SVG is 13 by 13 px; contained in its owner control. |
| blockquote | observed | [Actual icon](screenshots/after/blockquote.png). Visible, unobscured in 03-document-editing. SVG is 13 by 13 px; contained in its owner control. |
| text-box | observed | [Actual icon](screenshots/after/text-box.png). Visible, unobscured in notes-drawing. SVG is 13 by 13 px; contained in its owner control. |
| compose | observed | [Actual icon](screenshots/after/compose.png). Visible, unobscured in 01-shell. SVG is 11 by 11 px; contained in its owner control. |
| send-later | observed | [Actual icon](screenshots/after/send-later.png). Visible, unobscured in document-email. SVG is 13 by 13 px; contained in its owner control. |
| spam | not-observed | [Context only](screenshots/after/02-model-picker.png). Spam action requires a selected mailbox message. No email account or external mailbox was used. Screenshot shows the fixture model-picker context, not this unobserved glyph. |
| play | not-observed | [Context only](screenshots/after/chat-tool-fixture.png). The chosen Play glyph is a paused/completed TTS state. No speech service or audio playback was exercised. |
| pause | observed | [Actual icon](screenshots/after/pause.png). Visible, unobscured in feature-tasks. SVG is 10.07293701171875 by 10.07293701171875 px; contained in its owner control. |
| image | observed | [Actual icon](screenshots/after/image.png). Visible, unobscured in 03-document. SVG is 13 by 13 px; contained in its owner control. |
| image-export | observed | [Actual icon](screenshots/after/image-export.png). Visible, unobscured in editor-menu-file. SVG is 14 by 14 px; contained in its owner control. |
| rotate-right | observed | [Actual icon](screenshots/after/rotate-right.png). Visible, unobscured in editor-tool-transform. SVG is 14 by 14 px; contained in its owner control. |
| zoom-in | not-observed | [Context only](screenshots/after/02-model-picker.png). The Week zoom controls were not observed after opening Calendar in the disposable account. The action is unchanged apart from its glyph. Screenshot shows the fixture model-picker context, not this unobserved glyph. |
| line | observed | [Actual icon](screenshots/after/line.png). Visible, unobscured in notes-drawing. SVG is 20 by 20 px; contained in its owner control. |
| path | observed | [Actual icon](screenshots/after/path.png). Visible, unobscured in editor-tool-path. SVG is 18 by 18 px; contained in its owner control. |
| polygon | observed | [Actual icon](screenshots/after/polygon.png). Visible, unobscured in editor-tool-shape. SVG is 14 by 14 px; contained in its owner control. |
| rectangle | observed | [Actual icon](screenshots/after/rectangle.png). Visible, unobscured in editor-tool-shape. SVG is 14 by 14 px; contained in its owner control. |
| ellipse | observed | [Actual icon](screenshots/after/ellipse.png). Visible, unobscured in editor-tool-shape. SVG is 14 by 14 px; contained in its owner control. |
| shape-union | observed | [Actual icon](screenshots/after/shape-union.png). Visible, unobscured in editor-menu-layer. SVG is 14 by 14 px; contained in its owner control. |
| shape-subtract | observed | [Actual icon](screenshots/after/shape-subtract.png). Visible, unobscured in editor-menu-layer. SVG is 14 by 14 px; contained in its owner control. |
| shape-intersect | observed | [Actual icon](screenshots/after/shape-intersect.png). Visible, unobscured in editor-tool-marquee. SVG is 13 by 13 px; contained in its owner control. |
| shape-exclude | observed | [Actual icon](screenshots/after/shape-exclude.png). Visible, unobscured in editor-menu-layer. SVG is 14 by 14 px; contained in its owner control. |
| eyedropper | not-observed | [Context only](screenshots/after/editor-color-picker.png). The custom color-picker popover did not expose its Eyedropper glyph in this run. The active inline swatches are a different owner. This remains unverified. |
| gradient | observed | [Actual icon](screenshots/after/gradient.png). Visible, unobscured in 06-editor. SVG is 15 by 15 px; contained in its owner control. |
| color-swap | observed | [Actual icon](screenshots/after/color-swap.png). Visible, unobscured in 06-editor. SVG is 11 by 11 px; contained in its owner control. |
| merge-down | observed | [Actual icon](screenshots/after/merge-down.png). Visible, unobscured in 06-editor. SVG is 14 by 14 px; contained in its owner control. |
| merge-visible | observed | [Actual icon](screenshots/after/merge-visible.png). Visible, unobscured in 06-editor. SVG is 14 by 14 px; contained in its owner control. |
| flatten | observed | [Actual icon](screenshots/after/flatten.png). Visible, unobscured in 06-editor. SVG is 14 by 14 px; contained in its owner control. |
| rasterize | observed | [Actual icon](screenshots/after/rasterize.png). Visible, unobscured in editor-tool-text. SVG is 14 by 14 px; contained in its owner control. |
| lock | not-observed | [Context only](screenshots/after/editor-layer-properties.png). Toggle selected lock is a multi-selected-layer action. The fixture added layers but did not establish a multi-selection; this glyph remains unverified. |
| unlock | not-observed | [Context only](screenshots/after/02-model-picker.png). Unlock belongs to the Vaultwarden integration form. No vault authentication or unlock operation was performed. Screenshot shows the fixture model-picker context, not this unobserved glyph. |
| mask | observed | [Actual icon](screenshots/after/mask.png). Visible, unobscured in editor-tool-marquee. SVG is 14 by 14 px; contained in its owner control. |
| mask-add | observed | [Actual icon](screenshots/after/mask-add.png). Visible, unobscured in editor-menu-layer. SVG is 14 by 14 px; contained in its owner control. |
| mask-invert | observed | [Actual icon](screenshots/after/mask-invert.png). Visible, unobscured in editor-tool-inpaint. SVG is 12 by 12 px; contained in its owner control. |
| mask-clear | observed | [Actual icon](screenshots/after/mask-clear.png). Visible, unobscured in editor-tool-inpaint. SVG is 12 by 12 px; contained in its owner control. |
| selection | observed | [Actual icon](screenshots/after/selection.png). Visible, unobscured in editor-tool-pen. SVG is 12 by 12 px; contained in its owner control. |
| magnetic-lasso | observed | [Actual icon](screenshots/after/magnetic-lasso.png). Visible, unobscured in editor-magnetic-options. SVG is 14 by 14 px; contained in its owner control. |
| quick-select | observed | [Actual icon](screenshots/after/quick-select.png). Visible, unobscured in editor-tool-quickselect. SVG is 18 by 18 px; contained in its owner control. |
| subject-select | observed | [Actual icon](screenshots/after/subject-select.png). Visible, unobscured in editor-menu-selection. SVG is 14 by 14 px; contained in its owner control. |
| harmonize | not-observed | [Context only](screenshots/after/editor-tool-inpaint.png). Harmonize appears for a supported compositing workflow. Its label is retained. No ML harmonization service was invoked. |
| skew | observed | [Actual icon](screenshots/after/skew.png). Visible, unobscured in editor-tool-transform. SVG is 12 by 12 px; contained in its owner control. |
| perspective | observed | [Actual icon](screenshots/after/perspective.png). Visible, unobscured in editor-tool-transform. SVG is 12 by 12 px; contained in its owner control. |
| snapshot | observed | [Actual icon](screenshots/after/snapshot.png). Visible, unobscured in editor-history. SVG is 14 by 14 px; contained in its owner control. |
| history | observed | [Actual icon](screenshots/after/history.png). Visible, unobscured in 06-editor. SVG is 14 by 14 px; contained in its owner control. |
| histogram | observed | [Actual icon](screenshots/after/histogram.png). Visible, unobscured in editor-image-adjustments. SVG is 14 by 14 px; contained in its owner control. |
| hue-saturation | observed | [Actual icon](screenshots/after/hue-saturation.png). Visible, unobscured in editor-image-adjustments. SVG is 14 by 14 px; contained in its owner control. |
| black-white | observed | [Actual icon](screenshots/after/black-white.png). Visible, unobscured in editor-menu-image. SVG is 14 by 14 px; contained in its owner control. |
| invert | observed | [Actual icon](screenshots/after/invert.png). Visible, unobscured in editor-image-adjustments. SVG is 14 by 14 px; contained in its owner control. |
| desaturate | observed | [Actual icon](screenshots/after/desaturate.png). Visible, unobscured in editor-image-adjustments. SVG is 14 by 14 px; contained in its owner control. |
| auto-tone | observed | [Actual icon](screenshots/after/auto-tone.png). Visible, unobscured in editor-image-adjustments. SVG is 14 by 14 px; contained in its owner control. |
| auto-contrast | observed | [Actual icon](screenshots/after/auto-contrast.png). Visible, unobscured in editor-image-adjustments. SVG is 14 by 14 px; contained in its owner control. |
| auto-color | observed | [Actual icon](screenshots/after/auto-color.png). Visible, unobscured in editor-image-adjustments. SVG is 14 by 14 px; contained in its owner control. |
| blur | observed | [Actual icon](screenshots/after/blur.png). Visible, unobscured in editor-submenu-filter-presets. SVG is 14 by 14 px; contained in its owner control. |
| sharpen | observed | [Actual icon](screenshots/after/sharpen.png). Visible, unobscured in editor-tool-sharpen. SVG is 14 by 14 px; contained in its owner control. |
| drop-shadow | observed | [Actual icon](screenshots/after/drop-shadow.png). Visible, unobscured in editor-submenu-filter-presets. SVG is 14 by 14 px; contained in its owner control. |
| research-parallel | not-observed | [Context only](screenshots/after/02-model-picker.png). This is a comparison execution-mode icon, not the Deep Research navigation icon. No configured comparison models were available. |
| research-sequential | not-observed | [Context only](screenshots/after/02-model-picker.png). This is a comparison execution-mode icon. No configured comparison models were available. |
| memory-search | observed | [Actual icon](screenshots/after/memory-search.png). Visible, unobscured in chat-tool-fixture. SVG is 12 by 12 px; contained in its owner control. |
| skill-add | observed | [Actual icon](screenshots/after/skill-add.png). Visible, unobscured in skills-add. SVG is 14 by 14 px; contained in its owner control. |
| skill-test | not-observed | [Context only](screenshots/after/settings-users.png). The captured Test label is actually a webhook Test command in Admin, not skill testing. Its label remains, but the catalog alias needs renaming to webhook-test or a separate skill-test mapping before broader reuse. No webhook was created or sent. |
| sliders | observed | [Actual icon](screenshots/after/sliders.png). Visible, unobscured in feature-cookbook. SVG is 14 by 14 px; contained in its owner control. |
| keyboard | observed | [Actual icon](screenshots/after/keyboard.png). Visible, unobscured in editor-menu-help. SVG is 14 by 14 px; contained in its owner control. |
| logout | not-observed | [Context only](screenshots/after/02-model-picker.png). This Logout action belongs to Vaultwarden, not Odysseus account logout. No vault logout request was sent. Screenshot shows the fixture model-picker context, not this unobserved glyph. |
| user-add | observed | [Actual icon](screenshots/after/user-add.png). Visible, unobscured in settings-users. SVG is 14 by 14 px; contained in its owner control. |
| password-show | observed | [Actual icon](screenshots/after/password-show.png). Visible, unobscured in 00-login. SVG is 18 by 18 px; contained in its owner control. |
| ai-writing | observed | [Actual icon](screenshots/after/ai-writing.png). Visible, unobscured in 03-document. SVG is 13 by 13 px; contained in its owner control. |
| aspect-ratio | observed | [Actual icon](screenshots/after/aspect-ratio.png). Visible, unobscured in editor-tool-crop. SVG is 14 by 14 px; contained in its owner control. |
| crop-landscape | observed | [Actual icon](screenshots/after/crop-landscape.png). Visible, unobscured in editor-tool-crop. SVG is 14 by 14 px; contained in its owner control. |
| selection-add | observed | [Actual icon](screenshots/after/selection-add.png). Visible, unobscured in editor-tool-marquee. SVG is 13 by 13 px; contained in its owner control. |
| selection-invert | observed | [Actual icon](screenshots/after/selection-invert.png). Visible, unobscured in editor-tool-marquee. SVG is 14 by 14 px; contained in its owner control. |
| selection-reselect | observed | [Actual icon](screenshots/after/selection-reselect.png). Visible, unobscured in editor-menu-selection. SVG is 14 by 14 px; contained in its owner control. |
| selection-grow | observed | [Actual icon](screenshots/after/selection-grow.png). Visible, unobscured in editor-menu-selection. SVG is 14 by 14 px; contained in its owner control. |
| selection-similar | observed | [Actual icon](screenshots/after/selection-similar.png). Visible, unobscured in editor-menu-selection. SVG is 14 by 14 px; contained in its owner control. |
| align-distribute | not-observed | [Context only](screenshots/after/editor-layer-properties.png). Align/distribute selected layers requires a multi-selection. It is distinct from ordinary text alignment. The multi-selected state was not established. |
| transform-width | observed | [Actual icon](screenshots/after/transform-width.png). Visible, unobscured in editor-tool-transform. SVG is 12 by 12 px; contained in its owner control. |
| transform-height | observed | [Actual icon](screenshots/after/transform-height.png). Visible, unobscured in editor-tool-transform. SVG is 12 by 12 px; contained in its owner control. |
| image-generate | observed | [Actual icon](screenshots/after/image-generate.png). Visible, unobscured in chat-tool-fixture. SVG is 12 by 12 px; contained in its owner control. |
| file-png | observed | [Actual icon](screenshots/after/file-png.png). Visible, unobscured in editor-export. SVG is 14 by 14 px; contained in its owner control. |
| file-jpeg | observed | [Actual icon](screenshots/after/file-jpeg.png). Visible, unobscured in editor-export. SVG is 14 by 14 px; contained in its owner control. |

[Latest export aspect-ratio button alignment](export-placement/index.html): explicit centering removes the 4 px right / 2 px upward icon offset while retaining the input row and lock behavior.
