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
| --rail-w    | 60px |
| --content-max| 820px |
| --topbar-h  | 52px |

## Component Inventory

- **Rail button**: 40x40 icon button, active = accent-soft bg + accent color, hover tooltip right
- **Sidebar item**: conversation row, hover/active = surface bg, title ellipsis
- **Platform card**: grid card, border + surface bg, active = accent border + accent-soft bg
- **Note card**: bordered surface, panel header bar, markdown body, muted path footer
- **Message**: avatar + bubble, user right-aligned surface-2, assistant full-width
- **Composer**: input border container + accent send button, focus = accent border
- **Form field**: label + input, focus = accent border
- **Settings nav**: left rail of icon+label items, active = surface bg
- **Badge**: pill, accent-soft bg + accent text, variants: info/warn/danger
- **Toast**: bottom-center, surface-2 bg, border

---

## B2 验收记录（2026-10-26）

Playwright 桌面（1280×800）+ 移动（390×844）两视口截图验收，均通过：

- **无重叠**：rail(0–60) + sidebar(60–320) + main(320–1280) 在桌面正确铺满，三栏不重叠；移动端 rail 收窄到 52px，sidebar 改为 `position:fixed` 离屏（`transform: translateX(-105%)`），仅在 `.open` 时滑入，不与主区重叠。
- **文字不溢出**：`body.scrollWidth === body.clientWidth`，桌面 1280、移动 390，无横向滚动条。rail-btn 的 `.rail-tip` 为 `opacity:0` 绝对定位 tooltip，仅 hover 显现，不计入布局溢出（scrollWidth 检测为已知误报）。
- **流式消息不跳布局**：`.main` 为 flex 列 = `topbar(flex:none,52px)` + `scroll-area(flex:1,overflow-y:auto)` + `composer-wrap(flex:none)`。输入框恒定锚底，流式 delta 只在 scroll-area 内增长并自动滚动，不顶动 composer。
- **修一处布局缺陷**：VideoWorkspace 根元素复用了 `.app` 类名，作为 `.workspace` 的 flex 子项缺 `flex:1`，导致桌面端主区只占 657px、右侧留 623px 空白。补 `.workspace > .app { flex:1; min-width:0 }`，主区恢复铺满到 1280。
- **控制台零报错**：内联 SVG favicon（`data:` URI，知字 + #10a37f）消除 `/favicon.ico` 404。

截图存档：`phaseB-shots/` — auth_desktop / auth_mobile / app_desktop / app_mobile / settings_desktop / app_{notes,knowledge,media}_placeholder。
设计判断方法参考 design-taste-frontend 的 dial（DESIGN_VARIANCE 5 / MOTION_INTENSITY 3 / VISUAL_DENSITY 5），调性锚点见开头 North Star。