# 知记工作台 · 文档索引

> 更新时间：2026-10-27
> 当前目标形态：桌面 Web 工作台 + 自托管单机。手机端与 SaaS 多租户不作为 v0.1.0 交付项。

## 推荐阅读顺序

1. [产品定位](WORKBENCH_PRODUCT.md)：工作台是什么、服务谁、这一版做什么。
2. [技术基线](WORKBENCH_TECH.md)：当前技术栈、模块边界、数据处理与验收基线。
3. [五个工具说明](WORKBENCH_TOOLS.md)：每个工具的原理、技术栈、框架、代码入口与当前边界。
4. [数据 / API 现状](WORKBENCH_DATA_API.md)：已经落地的数据表和接口。
5. [前端改造计划](WORKBENCH_FRONTEND_PLAN.md)：阶段 A/B 与阶段 C 五工作区的进度和后续项。
6. [外部依赖](WORKBENCH_DEPS.md)：本机工具、在线服务与降级策略。
7. [部署指南](DEPLOY.md)：Docker Compose、本机运行、备份恢复、PostgreSQL 与 Obsidian。
8. [发布验收](RELEASE_ACCEPTANCE.md)：v0.1.0 验收范围、结果、已知边界与复跑命令。
9. [演示指南](DEMO.md)：从启动到生成笔记的短演示流程。

## 文档边界

- 本目录以工作台为唯一产品主轴。
- 旧的 Java 控制面阶段计划、旧总体架构与历史 ADR 不再维护，避免与当前工作台目标冲突。
- `control-plane/` 代码仍作为早期技术验证保留，但不在当前运行链路与发布验收范围内。
