# 知记工作台 · 前端改造计划书

> 版本：v0.1（2026-10-26） · 配套 `WORKBENCH_PRODUCT.md` / `WORKBENCH_TECH.md`
> 范围：先迁框架，再改 UI（改 UI 前先读 skill），最后逐个加工作区。

## 0. 速览

| 项 | 内容 |
| --- | --- |
| 现状 | 零构建 vanilla JS 单页（`web/static/app.js` 52KB + `app.css` + `config.html`），FastAPI 在 `/` 返回 `config.html`、`/static` 挂载静态资源 |
| 目标 | 改成 **Vue 3 + Vite + Pinia** 多工作台外壳；视频解析流迁成 ① 工作区；UI 重设计后逐个加 ②③④⑤ |
| 顺序 | 阶段 A 框架迁移 → 阶段 B UI 重设计 → 阶段 C 后续工作区 |
| 原则 | 不一次性重写；旧 vanilla 视频解析逻辑照搬进 Vue 组件，业务逻辑不动；每步可回滚 |

---

## 阶段 A · 框架迁移

目标：把单流 vanilla 应用换成 Vue 多工作台外壳，① 视频解析工作区在新框架里跑通。此阶段不碰视觉设计，先用朴素可用 UI 验证骨架。

### A1 脚手架与联调通路
- 在仓库根新建 `frontend/`，初始化 Vite + Vue 3 + Pinia。
- 配置 Vite dev server：`/api` 反代到本地 FastAPI（`:8000`），开发时前端跑 Vite、后端跑 uvicorn。
- 生产构建：`vite build` 产物落 `frontend/dist/`；FastAPI 改为托管 `dist/`（`/` 返回 `dist/index.html`，`/assets` 挂载静态），旧 `/static` 与 `config.html` 暂时保留不动，等 A4 再清。
- 跑通最简「Vite 首页 → 调 `/api/health`」确认通路。

### A2 工作台外壳
- 左侧竖排导航栏：五个工作区图标 + 短标签（① 视频解析 ② 笔记与待办 ③ 知识库 ④ 文档助手 ⑤ 媒体工坊），顶部全局区（用户名 / 设置 / 凭据）。
- Pinia store：`auth`（登录态）、`ui`（当前工作区）、`core`（models / settings / 平台凭据，复用现有 `/api/models` `/api/settings`）。
- 外壳只负责切工作区、把主区让给对应组件渲染；每个工作区是一个 Vue 组件。
- 鉴权沿用现有 cookie，Vite 反代后 cookie 同源，无需改后端。

### A3 迁移工作区①（视频解析）
- 把现有 `app.js` 的视频解析 + 会话 + 流式笔记生成逻辑迁成 `frontend/src/workspaces/video/` 下的组件与 composable。
- 业务逻辑（`api()`、`streamSSE()`、提交任务、查状态、读流式 delta）照搬，不重写。
- 会话列表降级为 ① 工作区内部子侧栏，不再是全局主结构。
- SSE 流式用 `fetch` + `ReadableStream` 读，Vue 组件用 `ref` 持有增量。
- 此阶段只迁 ①，②③④⑤ 在外壳里先放占位页（"建设中"），阶段 C 再实现。

### A4 验证与旧代码清理
- 跑通：注册 / 登录 → 选平台 → 提交链接 → 解析 → 生成笔记 → 对话 → 设置模型 / 凭据。
- 确认无回归后，把 FastAPI 入口切到 Vue 产物；旧 `web/static/app.js` / `app.css` / `config.html` 移到 `legacy/` 备份保留（不删，便于回滚对照）。
- 提交。

---

## 阶段 B · UI 重设计（改 UI 前先读 skill）

目标：定下工作台视觉语言并应用到外壳 + ① 工作区，给后续工作区立模板。

### B0 先读 skill
- 开工前先读 `oil-ui`（设计判断，中文场景契合）与 `design-taste-frontend`（反模板化、审计优先）两份 skill，按其方法做。
- 调性锚点：SaaS / 运营工具型，安静、密集、可反复操作；不要营销页、不要大 hero、不要装饰性卡片堆叠。
- 复用现有设计 token（`--bg` `--surface` `--accent #10a37f` 暗色工作调），不另起炉灶。

### B1 设计语言定稿
- 定：色板（暗色基底 + 一个强调色，避免单色族垄断）、字号阶梯（紧凑工作面，不用 hero 级）、间距栅格、卡片圆角 ≤8px、图标用 lucide-vue-next。
- 产出一份 `frontend/DESIGN.md`（token + 组件清单：侧栏项、工作区容器、列表行、表单、状态徽章、流式消息块）。

### B2 应用到外壳 + ①
- 重做外壳与 ① 工作区视觉，对齐 DESIGN.md。
- 用 Playwright 截图桌面 / 移动两个视口验收：无重叠、文字不溢出、流式消息不跳布局。
- 提交。此阶段结束才动 ②。

---

## 阶段 C · 后续工作区（按新框架 + 新 UI）

顺序与理由（依赖从少到多、能最快看到界面成型优先）：

1. **② 笔记与待办**：notes / todos / reminders / 日程 / 习惯打卡。依赖最少，纯 CRUD + 本地提醒轮询。
2. **③ 知识库**：项目库 / 文件上传 / 文档解析 / 全文 + 向量检索 / 资料问答。依赖解析与向量索引，复杂度最高，放第二。
3. **④ 文档助手**：总结 / 问答 / 生成 / 润色 / 批处理 / 转换导出。输入源接 ③ 的文件或本地上传，故排在 ③ 之后。
4. **⑤ 媒体工坊**：上传预览 / 字幕（Whisper）/ FFmpeg / 异步导出。依赖 FFmpeg 与长任务，放最后。

每个工作区走「表 + 仓储 + worker handler + Vue 组件」四件套，独立提交，互不阻塞。

---

## 不做清单（防越界）

- ❌ 不引 React / Svelte，定了 Vue 就不摇摆。
- ❌ 不引重型 UI 库（Element Plus / Ant Design）除非 B 阶段证明必要；先用原生 + token。
- ❌ 不在 A 阶段动视觉（A 是骨架验证，B 才是视觉）。
- ❌ 不在 B 阶段加新工作区（B 只立模板，C 才加工作区）。
- ❌ 不一次重写全部五个工作区；逐个迁、逐个提交。
- ❌ 不动后端业务逻辑（平台适配器 / 转写 / LLM / worker / 加解密），前端改造只动表示层与前端状态。
