# Zhiji Workbench Design Spec (DESIGN.md)

## North Star

> Quiet, dense, dark workbench - tools first, chrome last.

This is not a landing page or marketing page. It is a daily-use operational tool for a solo knowledge worker. High information density but organized. Built for scanning, comparing, repeated action.

**Delivery scope:** Phase C is a desktop web workbench. A native mobile app and a
mobile-specific feature set are not deliverables. Existing narrow-window
fallbacks keep the interface usable when a desktop browser window is resized,
but they are not acceptance targets.

## Design Dials

- Audience: personal knowledge worker (single-user local tool)
- Page type: operational workbench (NOT landing page)
- Vibe: quiet, restrained, pragmatic, dense-but-organized
- DESIGN_VARIANCE: 5 / MOTION_INTENSITY: 3 / VISUAL_DENSITY: 5

---

## Color

Keep dark + green brand identity. Avoid single-hue monopoly by adding cool-neutral background layers and an info-blue secondary accent.

| Token | Value | Usage |
|---|---|---|
| --bg | #1a1a1a | main background, slightly deeper for layering |
| --panel | #161616 | card/panel base |
| --sidebar | #121212 | nav rail and inner sidebar |
| --surface | #222222 | interactive surface (cards, rows) |
| --surface-2 | #2c2c2c | hover surface, secondary buttons |
| --input | #262626 | input field base |
| --border | #333333 | dividers, borders |
| --text | #e8e8ea | primary text |
| --muted | #9a9a9e | secondary text, labels |
| --accent | #10a37f | primary action color (brand green) |
| --accent-hover | #14b890 | accent hover |
| --accent-soft | rgba(16,163,127,0.14) | accent background (selected state) |
| --info | #5b8def | info-state blue (secondary, adds hue variety) |
| --danger | #ef4444 | error/delete |
| --warn | #f59e0b | warning |

Ban: purple gradients, glassmorphism, AI-default purple-blue gradients, single-hue monopoly.

## Typography Scale

| Token | Size | Usage |
|---|---|---|
| --fs-2xl | 20px | page title (settings head, auth h1) |
| --fs-xl  | 17px | markdown h1, section headers |
| --fs-lg  | 15px | brand name, markdown h2, rail brand |
| --fs-base| 14px | body text, default |
| --fs-sm  | 13px | sidebar items, step text |
| --fs-xs  | 12px | labels, hints, meta |
| --fs-2xs | 11px | badges, micro labels |

Font stack: system-ui, "Segoe UI", "Microsoft YaHei", sans-serif.
Monospace: Consolas, "Courier New", monospace.
Letter-spacing: 0. No negative tracking.

## Spacing Scale (4px base)

| Token | Value | Usage |
|---|---|---|
| --sp-1 | 4px  | micro gaps, badge padding |
| --sp-2 | 8px  | button gaps, small padding |
| --sp-3 | 12px | card padding, item gaps |
| --sp-4 | 16px | section padding, card body |
| --sp-5 | 20px | topbar padding, section spacing |
| --sp-6 | 24px | hero margin, section gaps |
| --sp-8 | 32px | settings padding, large gaps |

## Radius

All radii capped at 8px. No 12px/14px/16px rounded cards.

| Token | Value | Usage |
|---|---|---|
| --r-xs | 4px | inline code, badges |
| --r-sm | 6px | small buttons, icon buttons |
| --r-md | 8px | cards, inputs, composer, avatar |

## Layout Constants

| Token | Value |
|---|---|
| --content-max | 820px |
| --topbar-h   | 52px |
| --drawer-w   | 280px |
| --dock-h     | 56px |

## Shell Architecture (Phase B · Direction 1)

Replaced the generic 3-column icon-rail + sidebar + main shell with a **bento home + dot dock + focus mode + slide-out drawer** layout.

### Home: Bento Grid
- **`.shell`** — full-height flex column (`shell-top` brand bar + `shell-main` content area).
- **`.shell-top`** — 52px brand bar: logo + health + user (home only, hidden in focus mode).
- **`.bento`** — CSS grid, 4 columns × 2 rows, grid-template-areas:
  `"video notes knowledge" / "video docs media"`. The video tile spans 2 rows; all five tiles are live and surface real stats or recent activity.
- **`.bento-tile`** — bordered panel tile, hover = accent border + surface bg, active press = slight scale.

### Navigation: Dot Dock
- **`.dock`** — bottom-center floating pill (`position:absolute; border-radius:999px`), always visible.
- **`.dock-btn`** — icon button, labels (`.dock-label`) expand on hover/active via `max-width` transition.
- Home button + 5 workspace dots + settings + logout, separated by `.dock-sep` dividers.

### Focus Mode
- Clicking a bento tile sets `ui.focus` to the workspace key; `shell-top` hides, workspace fills `.focus-stage` (absolute inset:0).
- **ESC** → `ui.goHome()` returns to bento.
- **Ctrl+K** → toggles conversation drawer (video workspace only).

### Conversation Drawer
- **`.conv-drawer`** — off-canvas left drawer (`position:absolute; transform:translateX(-100%)`), slides in with `.open` class.
- **`.drawer-scrim`** — semi-transparent overlay, click to close.
- Contains brand header, new-chat button, search, conversation list, model mini.

## Workspace 02: Notes & Todos (Phase C)

### Desktop: three-pane workbench
- **Library rail (`250px`)** — searchable note index grouped into pinned / all notes, with a compact new-note action and local-save footer.
- **Editor canvas (`minmax(0, 1fr)`)** — borderless writing surface between the two rails, with title, tags, plain-text body, character count, save state and timestamp.
- **Todo flow (`304px`)** — capture form (priority / due date / optional note link), completion progress, open/done groups and per-item delete.
- The three panels use the shared border, panel and surface tokens instead of nested cards. At `1100px` the side rails narrow to `220px / 270px`.

### Mobile: one-pane state machine
- Below `920px`, `.notes-ws[data-pane]` exposes one of three stacked views: `library`, `editor`, `todos`.
- A three-button mobile nav keeps counts visible. The default pane is the note library; selecting or creating a note transitions to the editor.
- All panes respect the dock safe area and never introduce horizontal scrolling.

### Interaction contract
- Notes autosave after `700ms`; `Ctrl+S` flushes immediately and `N` creates a note when focus is outside an input.
- Switching/deleting first flushes pending edits. Delete requires a second click with a visible `确认删除` label.
- Todos support priority, due-date urgency, note association, toggle completion and a live completion bar. Deleting a note keeps its todos and clears `note_id` via `SET NULL`.
- Save state uses the existing info / warn / accent colors. Toasts sit above the dock instead of covering it.

### Added components
- **Note index row**: sequence number + title/excerpt, selected and pinned states.
- **Save status**: dot + text state machine (`保存中` / `有未保存修改` / `所有修改已保存`).
- **Tag chip**: compact removable token; enter or blur commits the tag input.
- **Two-step delete action**: idle icon expands into a danger confirmation label.
- **Todo row**: checkbox, priority dot, due label, linked-note jump and hover-only delete.
- **Todo progress bar**: thin accent track above the open/done list.

## Component Inventory

- **Bento tile**: bordered panel, icon + label + sub + body (live stats or recent activity), hover = accent border
- **Dock button**: pill icon button, label expands on hover/active, active = accent-soft bg
- **Workspace focus stage**: full-viewport workspace surface with a consistent back action and dock access
- **Conversation drawer**: off-canvas left panel, brand + new-chat + search + conv-list + model mini
- **Workspace topbar**: back button + drawer toggle + title + model select
- **Platform card**: grid card, border + surface bg, active = accent border + accent-soft bg
- **Note card**: bordered surface, panel header bar, markdown body, muted path footer
- **Message**: avatar + bubble, user right-aligned surface-2, assistant full-width
- **Composer**: input border container + accent send button, focus = accent border
- **Form field**: label + input, focus = accent border
- **Settings nav**: left rail of icon+label items, active = surface bg (full-screen overlay)
- **Badge**: pill, accent-soft bg + accent text, variants: info/warn/danger
- **Toast**: bottom-center, surface-2 bg, border

---

## B2 验收记录（2026-10-27 · Direction 1 重构）

Playwright + Chrome（1280×800）截图验收，均通过：

- **Bento 首页渲染**：5 个 bento-tile 正确铺满 grid-template-areas，视频瓦片跨 2 行。shell-top 品牌栏可见。
- **Dot dock**：7 个 dock-btn（首页 + 5 工作区 + 设置），底部居中浮动胶囊，hover 展开 label。
- **焦点模式切换**：点击视频瓦片 → 进入 `.focus-stage` 全屏模式，ws-main + ws-topbar + conv-drawer 正确渲染。shell-top 隐藏。
- **会话抽屉**：顶栏 Menu 按钮 / Ctrl+K → `.conv-drawer.open` 从左侧滑入，`.drawer-scrim` 遮罩点击可关闭。
- **ESC 返回首页**：按 Escape → 回到 bento 首页，shell-top 重新可见。
- **无横向溢出**：`body.scrollWidth === body.clientWidth`（1280 = 1280）。
- **控制台零报错**。

关键修复：之前 `App.vue` 导入的是 `components/AppShell.vue`（旧 rail 布局），而新 bento shell 误放在 `src/AppShell.vue`（根级，未被引用），导致 UI「跟没改一样」。已将新版覆盖到 `components/AppShell.vue` 并删除根级副本。同时 `style.css` 完全重写——移除旧 `.app`/`.rail`/`.sidebar`/`.menu-btn`/`.workspace`/`.placeholder-page` 规则，新增 `.shell`/`.shell-top`/`.bento`/`.dock`/`.focus-stage`/`.conv-drawer`/`.focus-placeholder` 等完整样式。

截图存档：`shot_home.png` / `shot_video_focus.png` / `shot_drawer_open.png` / `shot_after_esc.png`。
设计判断方法参考 design-taste-frontend 的 dial（DESIGN_VARIANCE 5 / MOTION_INTENSITY 3 / VISUAL_DENSITY 5），调性锚点见开头 North Star。

---

## C2 验收记录（2026-10-27 · 工作区② 笔记与待办）

Playwright + Edge（1280×800 / 390×844）截图验收，均通过：

- **桌面三栏**：笔记索引 `250px` / 编辑器自适应 / 待办流 `304px`，边框、间距与保存状态正常。
- **移动三页签**：笔记 / 编辑 / 待办单页切换，默认进入笔记列表，选中笔记后进入编辑器；无横向溢出。
- **笔记 CRUD**：新建、标题/正文/标签输入、700ms 自动保存、`Ctrl+S` 手动保存、置顶、两段式删除均通过。
- **待办 CRUD**：创建（优先级 / 截止日期 / 关联笔记）、完成切换、进度条更新、删除均通过；删除笔记后关联待办保留并断开 `note_id`。
- **快捷操作**：`N` 新建仅在非输入焦点生效；切换或删除前会先 flush 未落盘内容。
- **控制台与布局**：零报错；桌面和移动均无横向滚动；Toast 位于 Dock 上方，不遮挡操作区。
- **构建**：`npm run build` 通过，1585 modules transformed，CSS 42.68 kB，JS 147.14 kB（2.19s）。

截图存档：`shot_notes_desktop_final.png` / `shot_notes_mobile_final.png` / `shot_notes_desktop_todos.png` / `shot_notes_mobile_todos.png`。

---

## Workspace 03: Knowledge Base (Phase C)

### Desktop: archive / paper / retrieval rail
- **Library and document rail** — numbered libraries, per-library document index, import affordance and global stats. The left rail is an archive rather than a card list.
- **Reading canvas** — full source text rendered as a paper-like surface, with document metadata, reading progress, retrieval-hit highlighting and chunk navigation.
- **Retrieval rail** — full-text search, cited snippets and a streaming answer track. It can collapse to give the source document the full canvas.
- Search and answers can be scoped to the current library or all libraries. Clicking a result, source card or inline citation opens the original document at the matched range.

### Mobile: three-page state machine
- Below `920px`, the workspace exposes `资料库 / 阅读 / 检索问答` as three full-width tabs. Selecting a result transitions to the reader; `Alt+1/2/3` switches pages.
- The retrieval rail becomes a dedicated page instead of a squeezed side panel. All pages respect the dock safe area.

### Interaction contract
- Import supports TXT, Markdown, CSV, JSON and HTML with file picker or drag-and-drop; unsupported PDF/Office files return an explicit message.
- Library creation and two-step deletion use the same quiet affordance pattern as notes. Document rename and deletion live in the reader header.
- Search highlights the matched source range; citations use `[n]` tokens and source cards as two routes back to the source.
- Questions stream through SSE with a visible stop action. The retrieval threshold prevents unrelated questions from silently becoming model calls.
- `/` focuses search; `Ctrl+Enter` submits a question; all libraries, documents and chunks are isolated by user scope.

### Retrieval and storage
- Tables: `knowledge_libraries`, `knowledge_documents`, `knowledge_chunks`.
- Indexing uses Chinese bigram tokenization plus a deterministic hash-vector blend, with a relevance cutoff.
- The API exposes library/document CRUD, text import, scoped search, stats and SSE question answering.

## C3 验收记录（2026-10-27 · 工作区③ 知识库）

- **后端测试**：知识库、笔记、鉴权门禁与跨用户隔离共 `28 passed`；Ruff `All checks passed!`。
- **前端构建**：`npm run build` 通过，1586 modules transformed，CSS 68.78 kB，JS 178.95 kB（1.89s）。
- **首页入口**：知识库已从占位瓦片切换为真实统计入口，显示资料库、资料、切片与字符数。
- **响应式空状态**：已在本地服务中检查知识库空状态和三页签布局，资料库与资料索引分区正常，无卡片平铺回归。
- **服务**：当前代码已由 FastAPI 在 `http://127.0.0.1:8000` 托管。
- **截图存档**：`shot_knowledge_home_desktop.png`（当前为窄屏响应式截图，文件名沿用验收记录）。

---

## Workspace 04: Documents (Phase C)

### Desktop: register / paper / co-writing rail
- **Document register (`272px`)** — numbered document flow with title / body search, source marker, character count and local save state. The archive status strip summarizes documents, sourced drafts, characters and today's updates.
- **Writing canvas (`minmax(0, 1fr)`)** — warm-paper editing surface on a dark workbench shell. Horizontal ruling, a red binding margin, crop corners, page number and `DRAFT · 独立文档 / 取自笔记 / 取自知识库` metadata make it read as a working sheet rather than a generic textarea card.
- **Co-writing console (`320px`)** — a numbered action rail for summarize / ask / continue / polish, an operation-specific instruction block, a streaming result surface and explicit write-back controls. Results never overwrite text without a user action.

### Mobile: three-page state machine
- Below `920px`, the workspace exposes `文档 / 编辑 / AI` as three full-width pages. Selecting a document transitions to the editor; `Alt+1/2/3` switches pages directly.
- The register, paper canvas and AI rail each occupy the full content width instead of compressing into unreadable columns. All pages respect the dock safe area.

### Interaction contract
- Autosave runs after `700ms`; `Ctrl+S` flushes immediately. A local version counter and serialized save promise prevent stale requests from overwriting newer edits.
- New drafts can start blank or copy content from a note / knowledge document. Source metadata remains visible, but later edits do not mutate the source.
- AI requests send the current in-memory draft rather than waiting for autosave. Polish prioritizes the current textarea selection.
- Streaming exposes stop, retry and copy; write-back choices are insert at cursor, replace selection, or replace the full document. Full replacement uses a second confirmation state.
- `/` focuses document search; `Ctrl+Enter` executes the active AI action; delete and full replacement both use two-step confirmation.
- Markdown and plain-text export download directly from `/api/docs/{id}/export`; all queries and exports are user-scoped.

### Data and API
- Table: `documents` with `title`, `content`, `format`, `source_type`, `source_id`, timestamps and `user_id`.
- REST: document CRUD, search, stats, source options and export; SSE: `/api/docs/{id}/assist/stream`.
- Current boundary: single-document MVP only. Batch operations, complex format conversion and `doc_jobs` asynchronous processing are intentionally deferred.

## C4 验收记录（2026-10-27 · 工作区④ 文档助手）

- **后端测试**：文档、笔记、知识库、跨用户隔离与鉴权门禁共 `35 passed`；Ruff `All checks passed!`。
- **前端构建**：`npm run build` 通过，1589 modules transformed，CSS 101.21 kB，JS 206.99 kB（1.78s）。
- **浏览器闭环**：新建空白文档、标题 / 正文输入、700ms 自动保存、`Ctrl+S`、刷新后读回、从笔记取材、AI 总结、插入光标、复制结果和 Markdown 附件下载均通过。
- **首页入口**：文档瓦片已从 soon 状态切换为真实统计入口，显示文档数、取材数、字符数与今日更新数。
- **响应式布局**：窄屏下三页签 `文档 / 编辑 / AI` 均可用，无横向溢出；桌面实现登记册 / 纸面 / 协作轨三栏结构。
- **服务**：最新代码由 FastAPI 在 `http://127.0.0.1:8000` 托管。

---

## Workspace 05: Media Workbench (Phase C)

### Desktop: index / preview / processing track
- **Media library (`272px`)** — type filters, quick search, source rows with
  thumbnail or format glyph, media metadata, upload entry and two-step deletion.
  The rail remains an index rather than a gallery of cards.
- **Preview canvas (`minmax(0, 1fr)`)** — a dark inspection stage for image,
  audio and video sources, with dimensions/duration/codec metadata, inline
  rename, replace, download and direct processing entry points.
- **Processing and artifacts rail (`340px`)** — numbered actions for audio
  extraction, MP4 transcode, trim and optional subtitle generation; live task
  progress with cancellation; artifact preview/download; task history.
- Processing actions disable themselves when the selected media type cannot
  support the operation, and expose the reason instead of failing after submit.
- Drag-and-drop and the upload panel support single-file image, audio and video
  ingestion, with progress, duplicate-safe storage names and a 512 MiB limit.

### Interaction contract
- Selecting a media row updates the center stage without navigating away.
  `Ctrl/Cmd+K` keeps the workspace shortcut slot available for command search;
  `/` focuses library search when focus is outside an input.
- Trim uses explicit start/end seconds and validates them against the probed
  duration. Transcode produces H.264/AAC MP4. Audio extraction produces MP3.
- Long-running work is submitted to the shared task queue and polled without
  blocking the rest of the workbench. Failed tasks preserve a readable error in
  history and do not leave phantom artifacts in the rail.
- Source and artifact URLs are user-scoped. Preview and download responses use
  inline/attachment dispositions with UTF-8 filenames.

### Data and processing boundary
- Tables: `media_items` and `media_artifacts`; asynchronous jobs reuse the
  existing `tasks` table rather than introducing a parallel job system.
- Files live under `data/media/<scope>/<media-id>/`. Metadata records the
  original name, media type, MIME type, size, checksum and probe result.
- PyAV performs probing, audio extraction, MP4 transcode and trimming in
  process. No system FFmpeg executable is required. Optional `asr` dependencies
  enable faster-whisper subtitle generation.
- Current boundary: single-file source processing. Timeline editing, multi-track
  composition and batch queues are intentionally deferred.

## C5 验收记录（2026-10-27 · 工作区⑤ 媒体工坊）

- **交付范围**：以桌面 Web 工作台三栏闭环为准；窄屏切换仅作为浏览器窗口缩窄时的兜底，不属于本阶段验收项。
- **后端测试**：媒体 API 与处理服务 `11 passed`；全仓测试 `424 passed, 1 skipped`。
- **静态检查**：`ruff check src tests` 通过。
- **前端构建**：`npm run build` 通过，1589 modules transformed，CSS 132.48 kB，JS 237.23 kB。
- **媒体闭环**：图片/音频/视频上传与预览、音轨提取、MP4 转码、视频裁剪、任务进度与取消、产物播放/下载、历史记录与能力置灰均已验证。
- **数据隔离**：素材、产物、任务与文件路径均按用户作用域隔离；删除素材会级联清理产物记录和存储目录。
- **清理结果**：本轮 3 个测试素材与 3 个测试产物已删除，临时 `.media-smoke` 文件已移除，媒体统计恢复为空。
- **服务**：最新代码由 FastAPI 在 `http://127.0.0.1:8000` 托管。
