# Zhiji Workbench Design Spec (DESIGN.md)

## North Star

> Quiet, dense, dark workbench - tools first, chrome last.

This is not a landing page or marketing page. It is a daily-use operational tool for a solo knowledge worker. High information density but organized. Built for scanning, comparing, repeated action.

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
  `"video notes knowledge" / "video docs media"`. The video tile spans 2 rows (live workspace); notes is now live with counts; the remaining 3 are "soon" tiles with feature chips.
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

- **Bento tile**: bordered panel, icon + label + sub + body (live stats or soon chips), hover = accent border
- **Dock button**: pill icon button, label expands on hover/active, active = accent-soft bg
- **Focus placeholder**: centered card for "coming soon" workspaces, back button + feature chips
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
