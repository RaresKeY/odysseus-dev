# Workspace files beside chat

Last updated: 2026-11-08

## Scope

`routes/workspace_files.py`, included under `/api/workspace` by `routes/workspace_routes.py`, owns interactive file listing, bounded reads/downloads and explicit text saves. `static/js/workspaceFiles.js` owns the attached Files panel; `workspaceFileLinks.js` owns local-link parsing. `markdown.js` preserves local file links, `chatRenderer.js` links native file-tool icons, and `static/app.js` initializes the feature. Styling is in `static/css/workspace-files.css`; the service worker precaches this graph.

## Interaction

Files starts closed. One upper-right side-panel toggle opens the last selected Files or Decisions pane; the shared header switches between them and the toggle or close button collapses the pane. Files opens as a desktop side pane or a native full-screen modal at 900px and below. Selecting the active header tab retains the pane; arrow/Home/End keys switch available tabs. Close/Escape returns to the side-panel toggle or the initiating filename link. Clicking a filename in assistant inline code, a local Markdown link or a native read/write/edit tool icon opens that file. HTTP links, app hashes, scoped upload URLs, modified clicks and fenced executable code keep their existing behavior. Links request no file bytes until clicked.

The Workspace tab browses the currently selected vetted workspace, with folder breadcrumbs, a filter for the loaded folder, bounded directory entries and file sizes. Text defaults to read-only; Edit enables explicit Save or Ctrl/Command-S. Drafts survive file, tab, panel and chat changes in memory, with a browser navigation warning for unsaved changes. They do not survive reload. Clean drafts refresh from disk on reopening. Late reads cannot replace a newer file/chat/workspace selection.

Save submits a SHA-256 revision, preserves uniform CRLF files and file permissions, and keeps edits made during an in-flight save dirty. An external change produces a conflict without clearing the draft. No file creation, rename, deletion, command execution or watcher is exposed.

Copy, Download, wrapping, Markdown preview and raster image preview are available. HTML/SVG/source remain text; generated scripts are never run. PDF/DOCX can be explicitly imported through existing Documents routes. Text can be copied into a new stored document through More; the native Documents editor owns that copy, separately from the workspace file. The Documents tab lists the current chat's stored documents and opens the native editor. Opening Files and Documents closes the other pane.

Desktop width is a browser preference, with a draggable/keyboard-accessible separator and double-click reset. Narrow layouts put the file browser below the preview and contain focus in the native modal. Close/Escape restores the initiating control. Existing theme, fonts and native scrolling remain.

## Access and I/O

Every file route requires an interactive administrator or explicit auth-disabled single-user mode. Delegated API tokens are rejected before resolving paths. Workspace roots use existing mount mapping and vetting; files use the native file-tool confinement and sensitive/control-plane policy, additionally hiding private VCS/Codex metadata, dotenv variants and edit temporaries.

Listings omit symlinks, hardlinks and special files, return at most 500 entries and inspect at most 5,000 children. Reads accept at most 1 MiB of UTF-8 text; downloads accept at most 16 MiB and always use attachment disposition, octet-stream MIME, nosniff and no-store. POSIX reads pin no-follow ancestor descriptors and compare opened identity before reading. Fallback platforms validate opened identity and expose read-only files.

Saves require descriptor-relative no-follow support. They compare the loaded digest, write a temporary file inside the pinned parent, verify target/parent identity and replace the original. A process lock serializes application saves. This is optimistic conflict detection, not a cross-process atomic compare-and-swap or a filesystem watcher.

## Verification and limits

`tests/test_workspace_files.py` covers route admission, containment, sensitive files, races, hardlinks/special files, bounds, downloads, revision conflicts and byte/permission preservation. `tests/workspaceFileLinks.test.mjs` runs with preinstalled Deno without dependencies. `scripts/workspace_files_browser_tests.py` exercises the actual app with explicitly disposable Debian state, headless Chromium and desktop/390px/320px views; it reuses the existing private browser driver and invokes no provider.

The existing PDF importer can report an unavailable saved upload in auth-disabled mode; signed-in import is the qualified path. Native Windows/macOS filesystem behavior, files over the stated limits and arbitrary binary formats require separate qualification. Drafts are memory-only, the filter is nonrecursive, and text editing is disabled without the required descriptor operations.
