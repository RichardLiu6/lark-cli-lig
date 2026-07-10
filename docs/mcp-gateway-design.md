# lark-lig 产品化：MCP 隔离层设计

> 最后更新：2026-07-10
> 决策：面向"全公司低门槛下放给不爱折腾的人"，隔离层锚定 **方案 A（MCP 皮）**。
> 前置：威胁模型与 A/B 对比见 [permission-rollout-plan.md](./permission-rollout-plan.md)。

## 0. 为什么是 MCP（一句话决策依据）

把"怎么用 / 怎么守规矩 / 怎么接线"这套 **know-how 从每个客户端收敛到你服务端的 tool 定义里**：客户端只需"连一下 + login"，工具自动出现、带描述、自更新。对"人们懒得调教"这个前提，集中式（MCP）完胜分散式（CLI 皮 + skill/hook/settings 四件套需逐台同步）。

**唯一要付的纪律**：MCP tool result 必须 feedback-rich（见 §4），否则 MCP 会静默变成"反馈黑洞"，杀死 agent 自愈+建 skill 的能力。这份纪律是一次性、服务端、你可控的。

## 1. 目标与非目标

**目标**
- 员工用**自己的 Claude Code（或任何 MCP 客户端）**通过一个远程 MCP server 操作 Lark，零客户端配置。
- MCP server = **唯一安全边界**：认人 + RBAC + 审计 + 持密 + 限速 + 可吊销。
- 满足工具三要素：① 反馈保真 ② 日志可追溯 ③ eval specs。

**非目标**
- 不做"中心 agent 替员工干活"（那是 bot-v2 模型，见对比）；这里员工自带 agent。
- 不追求兼容非 MCP 的异构 agent（那是方案 B 的场景）。

## 2. 架构

```
员工的 Claude Code (MCP client)
        │  ① MCP over HTTPS + 每人 OAuth token
        ▼
┌─────────────────────────────────────────────┐
│  lark-lig MCP Server（唯一边界，跑在 VM）     │
│   · 认人：OAuth token → 员工身份               │
│   · RBAC：身份 → 允许的工具/参数（Firestore）  │
│   · 审计：每次调用落 logs                       │
│   · 持密：Lark app secret + 每人 Lark user token│
│   · 限速 + 高危二次确认                          │
│   · 只暴露"精选工具"，无 api 透传/无 bot 身份   │
└─────────────────────────────────────────────┘
        │  ② 内部调用（服务端带凭证）
        ▼
   lark-lig 执行后端（复用现有 CLI/库）
        │  ③
        ▼
     Lark Open API
```

- **复用 bot-v2 基建**：Firestore `permissions`（认人+RBAC）+ `logs`（审计）直接搬过来；VM 复用 `abl-bot-v2`（8.229.55.253，当前关停，需重新拉起）。
- **lark-lig 降级为执行后端**：MCP server 内部调它（进程内 import 或 subprocess），secret 只在服务端。

## 3. 工具面（curated allowlist —— 这是权限边界本体）

原则：**子工具 = 功能角色，不暴露原始能力**。绝不暴露 `api` 透传、绝不暴露 `--as bot`。

| MCP 工具 | 作用 | 可见角色 | 关键约束 |
|---|---|---|---|
| `lark_send_message` | 给同事发文本 | all | 以**调用者本人**身份发；收件人 email/open_id |
| `lark_send_file` | 发文件 | all（可加确认） | 同上；大文件/群发要 confirm |
| `lark_read_my_messages` | 读**自己**的私聊 | all | 只能读本人会话 |
| `lark_list_my_contacts` | 查**本部门**通讯录 | all | 非全组织导出 |
| `lark_list_my_approvals` | 查**自己**的审批 | all | 只本人提交/相关 |
| `lark_submit_approval` | 发起审批 | 按角色 | **open_id 服务端强制=调用者本人**（防冒名） |
| `lark_get_approval` | 查审批详情 | all | 限本人可见 |
| `lark_export_contacts` | 全组织通讯录导出 | **admin only** | 高敏，默认不给普通角色 |

**结果契约（每个工具统一返回，feedback-rich）**：
```json
{
  "ok": true/false,
  "stdout": "...",            // lark-lig 原始 stdout，原样
  "stderr": "...",            // 原始 stderr，原样（错误不吞）
  "exit_code": 0,
  "resolved_command": "lark-lig send ou_xxx ...",  // agent 可据此改参重试
  "error_code": "PERMISSION_DENIED",  // 稳定错误码（可选，叠加）
  "fix_hint": "该操作不在你的角色内，请联系管理员开通"  // 可选，叠加
}
```
失败时 MCP `isError=true`。**原始三件套（stdout/stderr/exit_code）雷打不动带出**；`error_code`/`fix_hint` 只叠加、不替换。稳定的 `error_code` 反而**加速 agent 建 skill**（有稳定契约可写）。

## 4. 反馈保真（三要素①，MCP 的必守纪律）

- MCP server 内部执行 lark-lig 时**捕获真实 stdout/stderr/exit**，塞进上面的契约原样回传。
- 禁止 `catch → "操作失败"` 式吞错。
- 回显 `resolved_command`，保住 agent"改个参数重试"的能力。
- 不可信调用方的越权信息按权限范围擦除（反馈范围跟着能力范围走），但**本人自己那次调用的诊断给全**。

## 5. 身份模型（关键细节，待拍板见 §9）

Lark 的两种身份决定"消息以谁的名义出现"：
- **审批**：API 用 bot token + payload `open_id` 表示提交人 → server 强制 `open_id=调用者本人`，天然正确归属。
- **IM 发消息**：要让消息显示"来自该员工本人"，server 需持有**该员工的 Lark user token**（员工 login 时一次 OAuth 授权给 server）。否则只能以 bot 名义发。

**推荐**：员工那"一次 login" = 对 MCP server 的 OAuth，**一箭双雕**：① 认证他是谁（喂给 RBAC）② 授予 server 一枚**可吊销的本人 scoped token**，用于以他本人身份发消息。仍是"一次登录"，不破坏零配置。

## 6. RBAC（复用 bot-v2 Firestore）

- `permissions` 文档：`{ name, role, allowed_tools[], scopes… }`（在 bot-v2 的 role/emails 基础上扩 `allowed_tools`）。
- 每次工具调用：token → 身份 → 查该身份 `allowed_tools` → 不在清单直接 `PERMISSION_DENIED`（带 fix_hint）。
- 工具**可见性**也按角色过滤（MCP `tools/list` 时只返回该员工能用的），既是权限也是 UX——agent 连"想调禁用工具"都不会尝试。
- 管理员在 Firestore 侧增删改角色/工具、**随时吊销** token。

## 7. 审计 + 限速 + 高危确认（三要素②）

- 每次调用一条 `logs`：`{ 时间, 员工身份, 工具, 参数摘要(脱敏), 结果码, lark_log_id }`，集中存储 ≥180 天。
- 限速：按员工 + 工具维度，防滥用/误刷。
- 高危动作（`submit_approval`、群发、`export_contacts`）：server 侧要求调用带确认标志，或触发 Richard 私聊实时告警。

## 8. 上手与零配置

- **远程 MCP + OAuth**：员工把 MCP server 加进 Claude Code 后 login 一次即可（Claude Code 支持远程 MCP server + OAuth —— ⚠️ 确切配置机制建站时验证）。
- **终极零配置**：用 managed/enterprise settings 把 MCP server 配置**中央下发**给所有员工，员工开机只 login（⚠️ Claude Code managed settings 能否下发 MCP server 配置，建站时验证）。

## 9. 待拍板的细节（探讨中）

1. **身份模型**：IM 发消息以"员工本人"（每人 Lark OAuth 交 server 托管）还是"以 bot 名义"？→ 倾向本人（§5），但要员工多授一次 Lark OAuth。
2. **首批工具集**：先上哪几个？建议 `send_message` + `list_my_approvals` + `submit_approval` 三个跑通闭环。
3. **执行后端接法**：MCP server 内部**进程内 import lark-lig**（快、无冷启动）还是 **subprocess 调 CLI**（隔离好、天然拿三件套）？
4. **eval 方式**（三要素③）：一个 harness 以"测试员工"身份连 MCP，断言"放行的能过 / 禁的被拦 / 反馈没被吞"。
5. **托管**：复用 `abl-bot-v2` VM 还是新起？secret/token 轮换与吊销流程。
6. **错误码表**：定一张稳定 `error_code` 枚举，让 agent 有稳定契约建 skill。

## 10. 分阶段

- **P1 骨架**：MCP server（3 工具）+ OAuth 认人 + Firestore RBAC + 审计 + feedback 契约；单个测试员工跑通。
- **P2 全量**：补齐工具集 + 限速 + 高危确认 + eval harness。
- **P3 铺开**：managed settings 中央下发 + 员工批量 onboarding + 吊销演练。
