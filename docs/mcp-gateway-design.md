# lark-lig 产品化：MCP 隔离层设计

> 最后更新：2026-10-29
> 决策：面向"全公司低门槛下放给不爱折腾的人"，隔离层锚定 **方案 A（MCP 皮）**。
> **[10-29] 决策：工具面升级为 MCP code mode**——MCP 只暴露 `lark_search` + `lark_execute` 两个工具，原"8 个精选工具"降为服务端 typed SDK 的方法；**权限边界从 `tools/list` 下沉到 SDK 绑定层**。§0 MCP 决策、§4 反馈保真、§5 身份模型、§6 RBAC 思想、§7 审计全部保留，只换载体。
> 前置：威胁模型与 A/B 对比见 [permission-rollout-plan.md](./permission-rollout-plan.md)。

## 0. 为什么是 MCP（一句话决策依据）

把"怎么用 / 怎么守规矩 / 怎么接线"这套 **know-how 从每个客户端收敛到你服务端的 tool 定义里**：客户端只需"连一下 + login"，工具自动出现、带描述、自更新。对"人们懒得调教"这个前提，集中式（MCP）完胜分散式（CLI 皮 + skill/hook/settings 四件套需逐台同步）。

**唯一要付的纪律**：MCP tool result 必须 feedback-rich（见 §4），否则 MCP 会静默变成"反馈黑洞"，杀死 agent 自愈+建 skill 的能力。这份纪律是一次性、服务端、你可控的。

### 为什么再升级到 code mode（2026-10 业界现状）

- **业界已收敛**：Stainless（2026-01）MCP server 只保留 code mode——两个工具 = docs search + execute，agent 写代码调 typed SDK；Cloudflare MCP portal（2026-03）默认 code mode，代码在隔离的 Dynamic Worker 里跑、凭证不进模型上下文；Redpanda / mcp-use 同构（search + execute + 沙箱）；Anthropic 工程博文 "Code execution with MCP" 给出两条核心收益：**渐进式披露**（按需检索方法签名，不把全部工具 schema 塞进上下文）、**中间结果留沙箱**（批量查询/过滤在沙箱内完成，只把结论回给模型）。
- **对我们的意义**：① 工具数从 8 涨到几十个时上下文成本不线性膨胀；② "查我的审批 → 过滤 → 逐个查详情"这类多步链路一次 execute 完成，不再一个 tool call 一轮往返；③ 新增能力 = 加一个 SDK 方法 + 一条 RBAC，客户端零变更。
- **代价 = 安全变成核心问题**：agent 生成的代码要在服务端跑，**必须真正沙箱隔离（进程/isolate 级），不能靠 import allowlist / 关键字黑名单**——那类"语言层过滤"一律视为可绕过（见 §3.3）。

## 1. 目标与非目标

**目标**
- 员工用**自己的 Claude Code（或任何 MCP 客户端）**通过一个远程 MCP server 操作 Lark，零客户端配置。
- MCP server = **唯一安全边界**：认人 + RBAC + 审计 + 持密 + 限速 + 可吊销 + **沙箱执行**。
- 满足工具三要素：① 反馈保真 ② 日志可追溯 ③ eval specs。

**非目标**
- 不做"中心 agent 替员工干活"（那是 bot-v2 模型，见对比）；这里员工自带 agent。
- 不追求兼容非 MCP 的异构 agent（那是方案 B 的场景）。
- 不给 agent 通用代码执行环境（沙箱里只有 `lark.*` SDK，不是"远程 shell"）。

## 2. 架构

```
员工的 Claude Code (MCP client)
        │  ① MCP over HTTPS + 每人 OAuth token
        │     只看到两个工具：lark_search / lark_execute
        ▼
┌───────────────────────────────────────────────────┐
│  lark-lig MCP Server（唯一边界，跑在 VM）           │
│   · 认人：OAuth token → 员工身份                     │
│   · lark_search：按角色过滤可见 SDK 方法             │
│   · lark_execute：把 agent 代码丢进沙箱 ──┐          │
│   · 持密：Lark app secret + 每人 user token（不进沙箱）│
│   · scope 自愈：缺 scope 只增不减发起增量授权（§5A）  │
│   · 审计 / 限速 / 高危确认                           │
│                                          ▼          │
│   ┌──────── 沙箱（isolate，§3.3）────────┐           │
│   │ agent 代码 → lark.* typed SDK stub   │           │
│   │ 无 FS / 网络只出 server 内部 / 限时限存│           │
│   └──────────────┬──────────────────────┘           │
│                  │ RPC（每次 SDK 调用）              │
│   ┌──────────────▼──────────────────────┐           │
│   │ SDK 绑定层 = 权限边界本体             │           │
│   │ RBAC 校验 → 注入身份/凭证 → 记 trace │           │
│   └──────────────┬──────────────────────┘           │
└──────────────────┼──────────────────────────────────┘
                   │  ② 内部调用（服务端带凭证）
                   ▼
      lark-lig / lark-cli 执行后端（复用现有 CLI/库）
                   │  ③
                   ▼
              Lark Open API
```

- **复用 bot-v2 基建**：Firestore `permissions`（认人+RBAC）+ `logs`（审计）直接搬过来；VM 复用 `abl-bot-v2`（8.229.55.253，当前关停，需重新拉起）。
- **lark-lig 降级为执行后端**：SDK 绑定层内部调它（进程内 import 或 subprocess），secret 只在服务端，**沙箱里拿不到任何凭证**，只拿到一个只能回调绑定层的 `lark` 对象。

## 3. 工具面（code mode —— 两个 MCP 工具 + 一套 typed SDK）

原则不变：**SDK 方法 = 功能角色，不暴露原始能力**。绝不暴露 `api` 透传、绝不暴露 `--as bot`——SDK 里**根本没有** `lark.api.raw()` / `lark.asBot()` 这类方法，沙箱里写什么代码都造不出来。

### 3.1 两个 MCP 工具

| MCP 工具 | 作用 | 关键约束 |
|---|---|---|
| `lark_search` | 按关键词/命名空间检索 SDK 方法签名 + 文档 + 示例（渐进式披露） | **按调用者角色过滤**：看不到无权方法（既是 UX 也是减少试探面）；返回 TS 类型签名，agent 可直接照写 |
| `lark_execute` | 在服务端沙箱执行 agent 写的代码（JS/TS），代码只能调 `lark.*` | 超时/内存上限；**每次 SDK 调用都在绑定层过 RBAC**；返回代码 stdout/stderr/exit + SDK 调用 trace（§3.2） |

> 可见 ≠ 授权：`lark_search` 的过滤只是 UX，**真正的拦截在 SDK 绑定层**——即使 agent 猜出隐藏方法名硬调，绑定层照样 `PERMISSION_DENIED`。

### 3.2 SDK 方法（原 8 个精选工具 → `lark.*`）

| SDK 方法 | 作用 | 执行身份 | 可见角色 | 关键约束 |
|---|---|---|---|---|
| `lark.im.sendMessage` | 给同事发文本 | **本人** | all | 以本人身份发；收件人 email/open_id |
| `lark.im.sendFile` | 发文件 | **本人** | all（可加确认） | 同上；大文件/群发要 confirm |
| `lark.im.readMyMessages` | 读自己的私聊 | **本人** | all | 只能读本人会话 |
| `lark.contact.listMyContacts` | 查本部门通讯录 | **本人** | all | 非全组织导出 |
| `lark.approval.listMine` | 查自己的审批 | bot + 本人 open_id | all | 只本人提交/相关 |
| `lark.approval.submit` | 发起审批 | bot + **强制本人 open_id** | 按角色 | 防冒名；绑定层覆写 `open_id`，代码传什么都无效 |
| `lark.approval.get` | 查审批详情 | bot | all | 限本人可见；审计绑定触发人 |
| `lark.contact.exportAll` | 全组织通讯录导出 | bot | **admin only** | 高敏；审计绑定触发人 |

**权限边界 = SDK 绑定层**：沙箱里的 `lark.*` 只是 stub，每次调用经 RPC 回到绑定层 → 校验身份 `allowed_methods` → 按 §5 注入执行身份/凭证 → 调执行后端 → 写 trace + 审计。RBAC 不再挂在 `tools/list` 上（那里永远只有两个工具）。

**结果契约（`lark_execute` 统一返回，feedback-rich）**：
```json
{
  "ok": true/false,
  "stdout": "...",              // agent 代码自身的原始 stdout，原样
  "stderr": "...",              // 代码原始 stderr / 未捕获异常栈，原样
  "exit_code": 0,               // 代码退出码；超时/OOM 有专属码
  "error_code": "SANDBOX_TIMEOUT",  // 代码级稳定错误码（可选，叠加）
  "trace": [                    // 每次 SDK 调用一条，按调用顺序
    {
      "method": "lark.im.sendMessage",
      "args_summary": "{to: ou_xxx, text: <42 chars>}",   // 脱敏摘要
      "resolved_command": "lark-cli im +messages-send --as user ...",  // 实际执行的命令
      "stdout": "...",          // 执行后端原始 stdout，原样
      "stderr": "...",          // 原始 stderr，原样（错误不吞）
      "exit_code": 0,
      "error_code": "PERMISSION_DENIED",   // 稳定错误码（可选，叠加）
      "fix_hint": "该操作不在你的角色内，请联系管理员开通",  // 可选，叠加
      "duration_ms": 312
    }
  ]
}
```
失败时 MCP `isError=true`。**两层原始三件套都雷打不动带出**：代码层（stdout/stderr/exit）+ 每次 SDK 调用层（resolved_command + stdout/stderr/exit_code）。即使 agent 代码 `try/catch` 把 SDK 报错吞了，trace 里照样有原始反馈——**隔离能力、不隔离反馈**。`error_code`/`fix_hint` 只叠加、不替换；稳定的 `error_code` 反而**加速 agent 建 skill**（有稳定契约可写）。

### 3.3 沙箱（agent 代码的执行环境）

**硬要求**（缺一不上线）：
- **真隔离**：进程 / V8 isolate / microVM 级隔离；**禁止**用 import allowlist、AST 过滤、关键字黑名单当安全措施（只能当 lint 提示）。
- **网络**：默认全拒；只允许回调 server 内部的 SDK 绑定层 RPC（Lark API 也由绑定层出，沙箱不直连）。
- **无文件系统**：无持久 FS、无宿主挂载；临时内存态随执行销毁。
- **无凭证**：环境变量/全局对象里不存在任何 token、secret；`lark` 对象是只能发 RPC 的 stub。
- **资源上限**：墙钟超时（初定 30s）、内存上限（初定 128MB）、单次执行 SDK 调用次数上限（防代码里循环刷接口，与 §7 限速叠加）。
- **一次一沙箱**：每次 execute 新建、跑完销毁，不跨员工、不跨调用复用状态。

**选型候选**（⚠️ 建站时选型验证，按"隔离强度 / 冷启动 / 能否锁网络 / 自托管成本"打分）：

| 候选 | 隔离方式 | 看点 |
|---|---|---|
| Deno（`--allow-net=<绑定层>`，无 `--allow-read/write/env`） | 进程 + 权限标志 | 最快落地，P1 本地首选；需叠加进程级限制（ulimit/cgroup） |
| V8 isolate（`isolated-vm` 等） | isolate | 冷启动 ms 级、内存可限；需自己补网络/RPC 桥 |
| Cloudflare Dynamic Worker | 托管 isolate | Cloudflare MCP portal 同款，出网可控、凭证不进沙箱；代价是引入 Cloudflare 依赖 |
| E2B | Firecracker microVM | 隔离最强；冷启动与费用较高，适合作上限方案 |

## 4. 反馈保真（三要素①，MCP 的必守纪律）

- SDK 绑定层执行 lark-lig 时**捕获真实 stdout/stderr/exit**，逐条写进 `trace` 原样回传；沙箱代码自身的 stdout/stderr/exit 同样原样回传。
- 禁止 `catch → "操作失败"` 式吞错——绑定层不吞，**agent 代码自己吞了也不影响 trace**。
- 每条 trace 回显 `resolved_command`，保住 agent"改个参数重试"的能力。
- 不可信调用方的越权信息按权限范围擦除（反馈范围跟着能力范围走），但**本人自己那次调用的诊断给全**。

## 5. 身份模型（已定：本人为默认；bot-only 操作走 bot，但由 MCP 按人限权）

三类操作，三种执行身份：

1. **普通操作 → 员工本人 user token**（发消息 / 读自己 / 查本部门）。员工 login 时一次 OAuth 把本人 scoped token 托管给 server；消息显示"来自本人"，归属天然正确。这个"一次 login"一箭双雕：① 认证他是谁（喂 RBAC）② 授 server 一枚可吊销的本人 token 用于代发。
2. **on-behalf 类 bot 操作 → bot token + server 强制注入本人 open_id**（如审批提交）。这类 API 设计只收 bot token，提交人靠 payload 的 `open_id` 字段；server 强制 `open_id=调用者本人` → 归属仍=本人、杜绝冒名。
3. **纯 bot 操作 → bot token**（如全组织通讯录导出，这类 API 只认 bot）。bot 拥有 app 全权，**但 MCP 层正是"按人切分 bot 权限"的地方**——员工永远拿不到 `--as bot` 裸权，只能触发被 allowlist + RBAC 授权给他的、包了一层的具体 SDK 方法，参数也受限。

> **bot 的全权只在服务端、被 SDK 绑定层切成粒度。** 员工不是"能用 bot"，而是"能用某几个内部走 bot 的 SDK 方法"。code mode 下这一点更关键：代码再灵活，能碰到的也只有绑定层放行的方法。

### 顺带解决一个 P0：bot 操作恢复可追责

bot 操作在 Lark 后台原本只记为"应用"、追不到具体人（见 [rollout-plan 的 T1](./permission-rollout-plan.md)）。现在所有 bot-backed 调用都**先过 MCP 认人**，server 审计把每次 bot 操作绑定到真实员工——bot 的全权被关进"每方法、每角色、可审计到人"的笼子里。这是把 bot 操作收拢到隔离层后**白捡的账责闭合**。

## 5A. 权限（scope）自愈（server 托管 user token 的必修课）

**动机（真实事故）**：2026-10-29 本地 lark-cli 发消息报缺 `im:message.send_as_user`。根因不是"没授过"，而是历次 `auth login --scope X`（不带 `--recommend`）会**整组替换** scope，每补一个就丢掉其它，问题反复出现。server 托管全员 user token 后，这个坑会按人数放大，必须在 server 侧一次性解决。

**机制**：
1. SDK 调用遇 `missing_scope`（Lark 原始报错 / 错误码）→ 绑定层计算 `目标 scope = 现有 scope ∪ 缺失 scope`，**只增不减**发起增量授权。
2. 本次调用返回 `error_code: "SCOPE_PENDING"` + `verification_url`（员工点开授权即可）+ `fix_hint`；**原始报错的 stdout/stderr/exit 照样在 trace 里带回**，不被替换。
3. 授权完成回调 → **diff 前后 scope**：新集合必须 ⊇ 旧集合 ∪ 缺失项，少任何一个即告警（Richard 私聊 + 审计 `logs` 记一条 `SCOPE_REGRESSION`），不静默接受。
4. 同一员工同一 scope 的待授权请求去重，避免一段代码循环触发多次授权链接。
5. 每次授权（前集合 / 请求集合 / 后集合 / diff 结果）写审计日志，可追溯"谁的 scope 在何时被谁改小了"。

**本地过渡方案（✅ 10-29 已落地）**：`~/.claude/scripts/lark-auth-add` 脚本 + PreToolUse hook `~/.claude/hooks/lark-auth-guard.sh`（eval `~/.claude/hooks/tests/test-lark-auth-add.py` 21/21）——拦截裸 `auth login --scope X`，改为"读现有 scope → 取并集 → 授权 → diff → 写日志"。**其并集 / diff / 日志逻辑将原样搬进 server 绑定层**，本地脚本的 eval 用例同步迁为 server eval（§9 ④）。

## 6. RBAC（复用 bot-v2 Firestore）

- `permissions` 文档：`{ name, role, allowed_methods[], scopes… }`（在 bot-v2 的 role/emails 基础上扩 `allowed_methods`，原 `allowed_tools` 改名，值为 SDK 方法名如 `lark.approval.submit`，支持 `lark.im.*` 命名空间通配）。
- 每次 **SDK 方法调用**（不是每次 MCP 工具调用）：token → 身份 → 查该身份 `allowed_methods` → 不在清单直接 `PERMISSION_DENIED`（带 fix_hint），写进 trace 并继续回给代码（代码可自行处理），审计记一条拒绝。
- 方法**可见性**也按角色过滤（`lark_search` 只返回该员工能用的方法），既是权限也是 UX——agent 连"想调禁用方法"都不太会尝试；但可见性不是边界，边界只在绑定层。
- 管理员在 Firestore 侧增删改角色/方法、**随时吊销** token。

## 7. 审计 + 限速 + 高危确认（三要素②）

- 两级日志：每次 `lark_execute` 一条（`{ 时间, 员工身份, 代码哈希 + 代码原文(加密存), exit_code, 耗时 }`）；其下每次 SDK 调用一条（`{ 方法, 参数摘要(脱敏), 结果码, resolved_command, lark_log_id }`），集中存储 ≥180 天。
- 限速：按员工 + SDK 方法维度，叠加单次 execute 内的调用次数上限，防代码循环误刷。
- 高危方法（`lark.approval.submit`、群发、`lark.contact.exportAll`）：绑定层要求调用带确认参数（如 `{confirm: true}`），或触发 Richard 私聊实时告警；无确认时返回 `CONFIRM_REQUIRED`，不执行。

## 8. 上手与零配置

- **远程 MCP + OAuth**：员工把 MCP server 加进 Claude Code 后 login 一次即可（Claude Code 支持远程 MCP server + OAuth —— ⚠️ 确切配置机制建站时验证）。
- **终极零配置**：用 managed/enterprise settings 把 MCP server 配置**中央下发**给所有员工，员工开机只 login（⚠️ Claude Code managed settings 能否下发 MCP server 配置，建站时验证）。

## 9. 细节决策

**① 身份模型 — ✅ 已定（2026-07-10）**：本人为默认；on-behalf 类 bot 操作走 bot + 强制本人 open_id；纯 bot 操作走 bot 但由 MCP 按人 allowlist + RBAC 限权、审计绑定到人（详见 §5）。

**② 工具面 — ✅ 已定（2026-10-29）**：code mode，MCP 只有 `lark_search` + `lark_execute`；权限边界 = SDK 绑定层（详见 §3）。

以下锁定到推荐默认，无异议即按此推进：

3. **首批 SDK 方法**：`lark.im.sendMessage` + `lark.approval.listMine` + `lark.approval.submit` 三个跑通闭环。
4. **eval 方式**（三要素③）：一个 harness 以"测试员工"身份连 MCP，跑 execute 代码断言四类——**放行的能过 / 禁的被拦（含硬调隐藏方法名、伪造 open_id、试图出网/读 FS/读 env）/ 反馈没被吞（代码 try/catch 吞错后 trace 仍有原始 stderr）/ scope 只增不减（模拟 missing_scope → 授权后 diff ⊇）**。改绑定层、沙箱配置、SDK 方法必跑。
5. **执行后端接法**：绑定层先用 **subprocess 调 lark-lig / lark-cli**（天然拿原始三件套 + 隔离，崩了不拖垮 server），性能有需要再优化成进程内 import。
6. **沙箱选型**：P1 本地先用 Deno 权限标志 + 进程级资源限制；P2 迁远程前按 §3.3 打分定型（⚠️ 建站时验证）。
7. **托管**：复用 `abl-bot-v2` VM（现关停需重拉）；token 轮换/吊销走 Firestore。
8. **错误码表**：定一张稳定 `error_code` 枚举（`PERMISSION_DENIED`/`TOKEN_EXPIRED`/`RATE_LIMITED`/`SCOPE_PENDING`/`SCOPE_REGRESSION`/`CONFIRM_REQUIRED`/`SANDBOX_TIMEOUT`/`SANDBOX_OOM`/`SANDBOX_NET_DENIED`…），让 agent 有稳定契约建 skill。

## 10. 分阶段

- **P1 本地 code mode（自用）**：本地 stdio MCP server，包 lark-cli + lark-lig 当执行后端；跑通 `lark_search` / `lark_execute` + 沙箱 + trace 契约 + scope 自愈（吸收 `lark-auth-add` 逻辑）+ eval harness（四类断言）；只 Richard 自己用。
- **P2 迁远程**：MCP over HTTPS 部署到 VM + OAuth 认人 + Firestore RBAC（`allowed_methods`）+ 两级审计 + 限速 + 高危确认；沙箱定型；单个测试员工跑通。
- **P3 铺开**：补齐 SDK 方法集 + managed settings 中央下发 + 员工批量 onboarding + 吊销演练。

## Sources

- Stainless — MCP servers now support code mode only（2026-01）：https://www.stainless.com/changelog/mcp-servers-now-support-code-mode-only
- Cloudflare — MCP portal code mode（2026-03-26）：https://developers.cloudflare.com/changelog/post/2026-03-26-mcp-portal-code-mode/
- Redpanda — Agentic Data Plane gateway code mode：https://docs.redpanda.com/agentic-data-plane/gateway/code-mode.md
- mcp-use — TypeScript client code mode：https://docs.mcp-use.com/typescript/client/code-mode.md
- Anthropic Engineering — "Code execution with MCP"（渐进式披露、中间结果留沙箱）
