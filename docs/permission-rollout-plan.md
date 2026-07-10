# lark-lig CLI 全公司下放：权限管控方案

> 最后更新：2026-07-10
> 适用范围：ABL ~20 人，7 部门（Production / Quality / Warehouse / Accounting / HR / Procurement / China-US）

## TL;DR

现有 CLI 直接下放 = 把整个 Lark 租户的"万能钥匙"复制 N 份。**唯一真正的安全边界是"把 APP_SECRET 从客户端拿掉"（＝中央网关）**。在此之前，客户端的任何角色限权都只是"防误操作的减速带"，不是访问控制——因为只要 secret 在机器上，懂技术的人改配置或直接调 API 就能绕过。

本仓库已落地 **Phase 1 客户端加固**（见下）作为立即见效的防误用层；Phase 2 网关是必须做的目标架构。

---

## 威胁模型（按严重性）

| 级别 | 威胁 | 机制 | 现状缓解 |
|------|------|------|----------|
| 🔴 P0 | APP_SECRET 泄露 → 人人可自铸 tenant token → 全 app 越权，且后台只记为"应用"、无法追责到人 | secret 明文随 CLI 分发 | ⏳ 需网关（Phase 2）；Phase 1 无法根治 |
| 🔴 P0 | 审批冒名：以他人名义发起付款/采购审批 | approval 走 bot token，提交人＝payload 里的 `open_id` 字段 | ✅ Phase 1：member 强制本人 open_id |
| 🔴 P0 | `api` 透传绕过一切命令级限制；`--as user` 遇 tenant-only 端点静默升 bot | `api` 命令 + 99991668 自动 fallback | ✅ Phase 1：member 禁用 `api` + 禁 `--as bot` |
| 🟠 P1 | 冒充他人发消息（钓鱼/内部诈骗） | `send <任意 open_id>` | ⏳ 需网关审计；Phase 1 仅本人身份 |
| 🟠 P1 | 全公司通讯录批量导出 | `users-all` | ⏳ 网关按角色收（Phase 3） |
| 🟠 P1 | 业务数据整表被扒/篡改 | Bitable 经 `api`；Base 设"组织内可编辑" | 🔒 手动收 Base 共享 + 网关数据域 |
| 🟡 P2 | 无集中审计/限速、误删无二次确认 | 日志只在本机 | ⏳ 网关（Phase 2） |

### 关键认知纠正
"user token 天然只能看自己的东西"这个假设**只对 IM 消息/个人文档成立**；在**通讯录、Bitable、审批**这三个高价值面上要么不生效、要么被 CLI 主动绕过。真正稳固的边界是 **app 被授予的 scope 并集**——要真收权必须在 app 粒度动刀（拆 app / 减 scope / 上网关）。

---

## Phase 1（已落地，本仓库）：客户端角色加固

**性质**：防误操作 + 修真 footgun，**不是安全边界**（secret 仍在客户端）。

### 角色模型（两档）
- `admin`（默认，未设 `LARK_LIG_ROLE` 或设为 `admin`）：完全不受限，供 Richard / 受控运维账户使用。**现有用法零影响。**
- `member`（任何非 `admin` 的值，如 `warehouse`/`production`）：
  - ❌ 不能 `--as bot`（tenant token）
  - ❌ 不能用 `api` 透传命令
  - 🔒 `approval submit` 强制用本人 open_id（填别人的会告警并覆盖）

### 实现位置
- `config.py` — `ROLE`（从 `LARK_LIG_ROLE` 读，默认 `admin`）
- `policy.py` — 单一策略中枢：`require_bot_identity()` / `require_raw_api()` / `allows_foreign_open_id()`
- `main.py` — `--as bot` 入口拦截（一处覆盖所有吃 `--as` 的命令）
- `commands/raw.py` — `api` 命令顶部拦截
- `commands/approval.py` — submit 时 member 覆盖 open_id
- `auth.py` — 顺带修复 `get_current_open_id`：OIDC 换 token 不返回 open_id，改为缺失时调 `/authen/v1/user_info` 取并回写缓存（防冒名依赖它，且自愈旧 token）

### 部署 member（临时过渡用法）
在员工机器的 `~/.lark-cli-lig/.env` 或环境变量里加一行：
```
LARK_LIG_ROLE=warehouse
```
> ⚠️ 员工可自行改回 `admin` 绕过——这就是为什么 Phase 1 只是减速带，必须尽快上 Phase 2。

---

## Phase 2（必做，目标架构）：中央网关

把 APP_SECRET 从所有客户端剥离，只存服务端（可复用现有 GCE VM `abl-bot-v2`）：

- **secret 只在服务端**，OAuth 授权码交换在服务端完成，客户端永不接触 secret（根除 P0-secret）
- 客户端换"瘦客户端"，只持**短时效、绑定本人**的 session token，请求打到网关
- 网关做四件事：**RBAC**（角色→命令/端点/数据域白名单）、**命令+端点白名单**（默认拒绝 `api` 任意透传）、**集中审计日志**（谁-何时-什么命令-什么参数-结果，≥180 天）、**限速 + 高危二次确认**
- approval 提交由网关强制 `open_id`＝已认证调用者本人（服务端根除冒名）

## Phase 3（纵深防御）
- 后端按角色拆多 Lark app，收窄每个 app 的 scope 并集
- 付款/采购/大额动作 → CLI 只能"发起 Lark 原生审批草稿"，人在 App 里终结（回归 Lark 自带审计+防冒名）
- Bitable 数据域按角色隔离到不同 Base/表，只读角色发只读 token

---

## 命令级权限矩阵（目标态，网关落地后）

✅ 允许 ｜ 🔒 限数据域/对象/需二次确认 ｜ ❌ 禁止 ｜ 📋 仅发起草稿不可终结

| 命令 \ 角色 | Production | Quality | Warehouse | Accounting | HR | Procurement | China-US | 管理层 |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| `send`（本人文本） | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `send-file`/`send-image` | 🔒 | 🔒 | 🔒 | 🔒 | 🔒 | 🔒 | 🔒 | ✅ |
| `send-group` | ❌ | 🔒 | ❌ | ❌ | 🔒 | 🔒 | 🔒 | ✅ |
| `read <chat_id>`（他人群） | ❌ | 🔒 | ❌ | ❌ | ❌ | 🔒 | 🔒 | 🔒 |
| `users-all`（全组织导出） | ❌ | ❌ | ❌ | ❌ | 🔒 | ❌ | ❌ | ✅ |
| `approval submit`（本人发起） | 📋 | 📋 | 📋 | 🔒 | 🔒 | 🔒 | 📋 | ✅ |
| `approval`（终结他人） | ❌ | ❌ | ❌ | 📋 | 📋 | ❌ | ❌ | ✅ 走 App |
| Bitable 读/写（经网关） | 🔒本域 | 🔒QC | 🔒库存 | 🔒财务 | 🔒HR | 🔒采购 | 🔒订单 | ✅ |
| `api`（透传） | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | 🔒审计内 |
| `--as bot` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | 🔒运维账户 |

---

## 如果只做三件事（最小行动清单）

1. **把 APP_SECRET 从每台员工机器删掉，只留一台受控机/VM** — 唯一根除"人人可铸 tenant token"的动作。⚠️ 但这会让客户端 CLI 无法独立工作（token 刷新也要 secret）→ 直接引出必须做网关（Phase 2）。
2. ✅ **给非管理员发 member 角色 CLI**（`LARK_LIG_ROLE=<部门>`）— 已实现，堵掉 `api` 透传、`--as bot`、审批冒名。
3. **关键审批终结改回 Lark App 手点，Bitable Base 从"组织内可编辑"降级** — 手动可做，压住冒名与数据篡改。

> 状态：第 2 件已在本仓库完成（Phase 1）。第 1、3 件是 ops + 网关工作，待决策后推进。
