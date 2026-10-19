# D5: 凭据加密用 cryptography + 环境主密钥

- **状态**: 已采纳
- **日期**: 2026-10

## 背景

用户的 LLM API Key 和平台 Cookie 是高敏感凭据，不能明文存储。自托管单机场景下没有 KMS / Vault 等密钥管理服务，但环境变量 + AES-GCM 可以提供足够的保护：密钥不入库、不进 Git，加密数据在库中即使泄露也无法解密。

## 决策

LLM API Key 与平台 Cookie 使用 cryptography 库的 AES-256-GCM 加密入库，主密钥从环境变量读取。

## 备选方案

1. HashiCorp Vault——专业密钥管理，但单机自托管引入额外服务。2. 云 KMS（AWS/GCP）——依赖云平台，与自托管理念冲突。3. Fernet 对称加密——cryptography 库提供，但 AES-GCM 更标准且支持 AAD（附加认证数据）绑定平台和作用域。

## 推翻条件

上云后用 KMS/Vault 管理主密钥；加密算法本身不需改动，只需替换密钥来源。
