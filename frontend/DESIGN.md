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
  `"video notes knowledge" / "video docs media"`. The video tile spans 2 rows (live workspace); the other 4 are "soon" tiles with feature chips.
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
