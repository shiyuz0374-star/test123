---
name: crm-auto-capture
description: 当用户输入销售拜访记录、电话沟通纪要、客户跟进要点时自动触发。识别自然语言中的客户名/联系人/电话/需求/下一步/deadline，通过 ticket-mcp 的 CRM MCP 工具自动录入。当 OpenClaw 收到的对话内容明显包含"今天拜访了..."、"刚和...通了电话"、"明天约..."、"客户答应..."等销售场景短语时启用此 skill。
---

# crm-auto-capture

销售方（销售）通过对话发销售拜访 / 跟进纪要时，**不写 prompt**——OpenClaw 自己识别字段 + 调工具录入。

## 何时用

用户消息符合以下任一信号：
- "今天/刚才/今早拜访了 XX..."
- "刚和 XX 通了电话/微信..."
- "明天/下周要去 XX 拜访 YY..."
- "XX 答应下周四前发方案/报价/资料..."
- "客户想先看..."、"客户希望..."
- "XX 关注..."、"XX 关心..."

**不**用于：
- 内部技术问题（用对应技术 skill）
- 非销售场景（闲聊、状态查询）
- 已经结构化的字段（直接调工具，不用 skill）

## 工作流

### Step 1：抽取字段

从用户消息中识别：customer_name, contact_name (+role), phone (缺则不假装), need, next_action, deadline, talked_at。

### Step 2：时间词解析（北京时间 UTC+8）

| 用户说 | 解析为 |
|---|---|
| 本周三前 | 本周三 23:59:00+08:00 |
| 下周四 / 下周四前 | 下周四 23:59:00+08:00 |
| 两天内 | today + 2 day 23:59:00+08:00 |
| 下个月中旬 | 下月 15 日 18:00:00+08:00 |
| 本周内 | 本周日 23:59:00+08:00 |
| 今天 | today EOD |

**agent 应该主动获取当前日期**算相对偏移，**不要凭空猜**。

### Step 3：调 MCP 工具（按顺序）

⚠️ **必须走真·MCP 路径**（通过 catalog 调 MCP.ticketMcp.*），不要直接调 Python 函数。

a. **去重检查**：先调 MCP.ticketMcp.searchCustomer + searchContact

b. **创建缺失的实体**：按需调 MCP.ticketMcp.createCustomer / createContact

c. **记录沟通纪要**：调 MCP.ticketMcp.createFollowUp，content 写完整沟通纪要

d. **创建承诺待办**：调 MCP.ticketMcp.createTodo，due_at 用 Step 2 解析出的时间

### Step 4：缺失字段的**诚实标注**

**绝不**编造电话、邮箱、预算、日期。处理：
- 字段缺失 → 在 notes / description 里标 ⚠️ XX 未提供
- 在最终报告里**显式列出**缺失字段
- 时间模糊（如"下个月中旬"） → 解析为合理日期，备注里说"约 X 月中旬"

### Step 5：报告

每步报告：**工具名 + 返回 ID + isError**

例：
[1] searchCustomer → 未找到
[2] searchContact → 未找到
[3] createCustomer → CUS-1790736787380-0000
[4] createContact → CON-1790736787424-0000
[5] createFollowUp → FUP-1790736787465-0000
[6] createTodo → TODO-1790736787506-0000

最后给出 **4 个 ID**（或说明哪些没建因为已存在）+ **缺失字段清单**。

## 约束

- ✅ 用真·MCP 工具（catalog 路径），不要走 Python 直调
- ✅ 先 search 后 create，避免重复
- ✅ 缺失字段显式标注，不假装录入
- ✅ 时间词必须用 agent 当前时间算，不准猜
- ✅ 报告里包含 isError 标志
- ❌ 不要试图推断敏感字段（电话 / 邮箱 / 预算）
- ❌ 不要因为缺失字段就跳过整个流程——能建的先建