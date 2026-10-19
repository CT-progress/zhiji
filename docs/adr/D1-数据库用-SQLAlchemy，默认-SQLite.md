# D1: 数据库用 SQLAlchemy，默认 SQLite

- **状态**: 已采纳
- **日期**: 2026-10

## 背景

项目初期需要快速验证多平台知识加工流水线，数据库选型要在「零运维」和「可扩展」之间取平衡。SQLite 是进程内嵌入式数据库，不需要独立部署服务；SQLAlchemy 是 Python 生态最成熟的 ORM，支持多种后端切换。

## 决策

数据层使用 SQLAlchemy ORM，默认 SQLite 作为数据库引擎。

## 备选方案

1. 直接用原生 SQL + sqlite3 标准库——可避免 ORM 抽象泄漏，但手写映射代码量大、类型安全靠人工保证。2. 用 Tortoise ORM / Peewee 等轻量 ORM——社区生态不如 SQLAlchemy，迁移工具和测试支持较弱。3. 一开始就上 PostgreSQL——功能更强，但需要独立部署数据库服务，增加自托管复杂度。

## 推翻条件

多用户并发写入出现瓶颈时（SQLite 写锁是全库级），通过改 `ZHIJI_DATABASE_URL` 切 PostgreSQL，代码无需改动。
