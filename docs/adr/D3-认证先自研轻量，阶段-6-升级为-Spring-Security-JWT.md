# D3: 认证先自研轻量，阶段 6 升级为 Spring Security JWT

- **状态**: 已采纳
- **日期**: 2026-10

## 背景

自托管单用户场景下，部署 Casdoor / Keycloak 等身份提供商的运维成本远高于收益。自研 bcrypt + 签名 cookie 方案在 50 行代码内可覆盖注册/登录/登出/数据隔离，且无外部依赖。

## 决策

MVP 阶段自研 bcrypt 密码哈希 + HMAC 签名会话令牌；阶段 6 升级为 Spring Security JWT 资源服务器。

## 备选方案

1. Casdoor / Keycloak——功能完备（OAuth2/OIDC/SAML），但需要独立部署和管理一套身份系统。2. Authlib / itsdangerous——已有库可签发 token，但仍需自建用户管理和权限逻辑。3. 无认证——不满足多用户数据隔离要求。

## 推翻条件

需要对外开放注册时，引入 Casdoor 或升级为 OAuth2 提供商；阶段 6 已完成 Spring Security JWT 升级。
