# D12: Obsidian 接入取直接写 vault 目录

- **状态**: 已采纳
- **日期**: 2026-10

## 背景

自托管单机形态下，应用与 Obsidian vault 在同一台机器上，直连文件系统零依赖、免装插件。ARCHITECTURE.md §3.1 推荐的 Local REST API 方案只在跨机时才必要。直写目录配合幂等 upsert（按 `zhiji_id` 前置去重），已能满足需求。

## 决策

Obsidian 接入采用直接写 vault 目录的方式，而非 Local REST API 插件。

## 备选方案

1. Local REST API 插件——需要 Obsidian 安装插件并开放 API 端口，单机场景下多了一层网络开销和依赖。2. Obsidian Sync——商业服务，与自托管理念冲突。3. Git 同步——延迟高，且 Markdown 写入 + Git 提交的冲突处理复杂。

## 推翻条件

进入多机 / 云部署时（届时补 5.5.4 的 REST 后端），需要跨机器访问 vault。
