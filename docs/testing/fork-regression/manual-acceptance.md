# Manual acceptance for the fork

Use this after the automated smoke and deployed browser checks. The operator URL is http://127.0.0.1:7824/. Record the full source from `/api/version`, browser/device, chosen endpoint/model and pass/fail evidence. Use a fresh chat for each model. These are manual tests; automated synthetic smoke does not establish real-model success.

The complete 154-case procedure/prerequisite/evidence catalogue is [cases.json](cases.json). The checks below prioritize the ordinary user journey, then route the remaining product areas. Mark unsupported integrations **blocked** or **not run**, never passed. Keep test credentials in their existing private store.

## First pass: everyday chat

| Check | Steps | Expected result |
| --- | --- | --- |
| Access and saved settings | Open the URL, sign in, reload, sign out/in. | Existing account, chats, providers and model assignments remain. No setup/reset screen. |
| Sidebar | Start a fresh browser profile; expand/collapse Chats, then reload after choosing a state. | Chats starts expanded; explicit saved state wins; readable labels/icons; plain sidebar toggle. |
| Composer | Type enough text for several lines; try desktop, narrow desktop and phone width. | Model/Auto and Agent/Chat controls stay visible, usable and inside the composer. |
| Permission menu | Open/close it repeatedly with mouse and keyboard; select Always Ask, reload that chat, then create another chat. | No flicker; state icons and selected mode agree; old chat retains Always Ask; new chat starts Auto-Approve. AP-01/02/04, UI-01/02. |
| Auto persistence | Configure intended Auto models/efforts once. Create two new chats and reload. | Assignments persist; no silent Gemini 2.5 fallback. Actual response model and context model match the routed model. UI-07. |
| Greeting | Send “sup” separately with Auto, Gemini, Gemma and the saved OpenAI model. | One useful final answer; no repeated-tool guard on a greeting, duplicated greeting or unsupported-effort error. |
| Message actions | Copy your message; open its More menu and edit/resend. Open assistant More and use Rewrite shorter. | Copy retains the intended text; Edit and Rewrite shorter are in More; actions target the selected message. |
| Scroll and windows | Scroll a long conversation, stream a reply, drag/resize a tool window, switch theme/density. | Stable text size, no unexpected zoom/jump, reachable controls and acceptable drag responsiveness. UI-04/05/10/12. |
| Reload/offline | Reload after deployment; test offline shell only after a successful online load. | Current source/assets load; no stale module or precache error. Network-dependent features report unavailable. UI-09. |

## Agent and file workflow

Use an ordinary scratch workspace and a unique file such as `acceptance-<date>-<model>.txt`. Preserve other files. Repeat the basic creation flow on Auto and each intended real provider; provider quota failures are separate failures, not reasons to claim tool success.

1. Ask “Make a file and write in it.” If asked for details, answer “anything.” Expect a successful write in the selected workspace, successful final response, and matching Decisions entries. Verify actual bytes in Files. This is FS-15.
2. Ask explicitly to create the unique file, write a short Unicode text containing a fenced code block, read it back, then delete it. Inspect every tool receipt and confirm the file is absent. FS-01/04.
3. Ask to create a *document* with another unique name. Confirm whether the app created a workspace file or a native stored document, open the corresponding viewer and verify its content. The final response must describe the artifact that actually exists. FS-14.
4. Click a filename in the assistant message and in a file-tool result. Open Files from the common right-side control. Browse a folder, use breadcrumbs/filter, switch between Files and Decisions, collapse and reopen. The app should retain the selected pane and return keyboard focus to the initiating control.
5. Open a small text file, enable Edit, save Unicode/CRLF text, download and compare bytes. Change the same file externally after loading it; try saving the old draft. Expect a conflict, an intact draft and no overwrite of the external edit.
6. Open a different file/chat while an edit is unsaved, then return. Expect the in-memory draft to remain; browser navigation warns. Drafts are not promised to survive reload. Try Markdown/image preview; HTML/SVG/source must not execute.
7. In Always Ask, request a scratch write, approve that exact action once and confirm only that action runs. Reject another request and confirm no mutation. An expired approval must request a fresh action. AP-10/11.
8. In Auto-Approve, inspect a workspace read and a write. Reads should show native permission checks; writes/commands should show the actual reviewer or explicit human decision. Uncertainty/denial must not silently become permission.
9. Start a harmless long-running scratch task and press Stop. Confirm the turn and child process end; reloading must not resume it. Run deeper process-tree checks only in the disposable fixture.
10. Check a missing file and a private configuration path using controlled scratch fixtures. Expect bounded errors and no private content. Binary files should offer Download rather than execute or masquerade as text. FS-07/09/13.

## Product-area pass

| Area | Manual action and observable result |
| --- | --- |
| Notes | Create, edit, reload, search and delete a scratch note; undo/redo and keyboard shortcuts work. |
| Documents | Create/import a supported scratch document, edit, inspect versions, restore and export. Test unavailable/corrupt input without losing the current document. |
| RAG/uploads | Upload a small known file, verify attachment bytes and ask a grounded question if a vector service is provisioned. Record unavailable Chroma as blocked. |
| Compare | Compare two intended endpoints, verify both actual model identities and reveal/vote/history. |
| Search/research | Search a distinctive query and inspect actual sources; create/read/delete only your scratch research report. Check unavailable-provider behavior. |
| Memory/skills | Add/read/search/delete a synthetic fact; create/edit/test a scratch skill and verify cancellation/error reporting. |
| Calendar | Create/edit/delete a scratch event; verify a multi-day event, an exclusive midnight end and a recurring source-timezone event across DST. |
| Tasks | Create a disabled scratch task, inspect next-run/timezone, edit and delete. Test execution only against synthetic destinations. |
| Email/contacts | Use a dedicated test mailbox: open/search/mark/move/reply, reject an invalid move and verify the reader remains. Sync/edit contacts only in test address books. |
| Gallery/editor | Use a scratch image; open/edit/undo/export, verify mobile layout and error recovery. Real generation requires the chosen provider. |
| Speech | Check microphone permission denial, short transcription, TTS start/stop and the optional Granite endpoint if provisioned. |
| Cookbook | Inspect hardware/model-fit and saved state. Downloads, GPU serving and native Windows checks require their explicit fixtures; do not infer them from CPU smoke. |
| Settings/integrations | Save one harmless setting and reload; verify retained model registrations. Exercise MCP, pairing/webhooks, backup/restore and account isolation only with their declared disposable fixtures. |
| Recovery | Restart the prepared deployment through its operator workflow, reconnect the bridge and confirm the same account/data/models/source. Restore testing uses a copy of state. |

## Record results

For each failure retain the catalogue ID, source SHA, exact request, model/endpoint, mode, workspace, visible error/HTTP status, sanitized Decisions export and screenshot. Note any actual file/tool effect even when the final model response failed. Do not retry an ambiguous mutation until inspecting its receipt and disk state. Earlier failed runs remain part of the record after a later pass.

The larger historical audit is not a certification of these flows. Security/multi-owner, optional-service and platform procedures remain independent qualification until tested at the final source.
