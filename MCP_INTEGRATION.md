# MCP_INTEGRATION.md

本文件说明本项目实际使用的麦当劳 MCP Server、调用到的 Tool、调用流程与业务价值。

---

## 1. MCP Server 基本信息

| 项目 | 值 |
|---|---|
| Server 地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP（远程托管） |
| 认证方式 | 请求头 `Authorization: Bearer <MCP_TOKEN>` |
| Token 获取 | 麦当劳开放平台 `https://open.mcd.cn/mcp` → 手机号登录 → 控制台 → 激活 → 同意服务协议 |
| 限流 | 每个 Token 每分钟最多 600 次请求，超出返回 429 |
| 错误码 | 401 Token 无效/过期/缺失；429 触发限流 |

本项目**不内置**任何 Token，配置中一律使用环境变量占位符 `${MCD_MCP_TOKEN}`。

---

## 2. 实际使用的 Tool 清单

本 Skill 按需调用以下 12 个 Tool，覆盖"时区判断 → 选品 → 优惠 → 核价 → 下单"全链路：

| Tool 名称 | 在本项目中的用途 | 调用阶段 |
|---|---|---|
| `now-time-info` | 获取当前完整时间，作为时区换算基准 | Step 1（必调） |
| `query-nearby-stores` | 根据用户位置查找可服务门店 | Step 2 |
| `delivery-query-addresses` | 查询用户已创建的配送地址（跨时区代点场景） | Step 6 |
| `delivery-create-address` | 新增配送地址（跨时区代点场景） | Step 6 |
| `delivery-query-stores` | 查询指定收货地址可配送的门店 | Step 6 |
| `query-meals` | 拉取当前门店在售餐品菜单（含分类、餐品编码、标签） | Step 2 |
| `query-meal-detail` | 查询餐品详情与套餐组成、可替换选项 | Step 2 |
| `list-nutrition-foods` | 获取餐品营养成分（能量、蛋白质、脂肪、碳水、钠、钙） | Step 2 / Step 3 |
| `available-coupons` | 查询当前可领取的麦麦省优惠券 | Step 2 |
| `query-my-coupons` | 查询账户下已持有的优惠券 | Step 2 |
| `query-store-coupons` | 查询当前门店可用优惠券 | Step 2 |
| `calculate-price` | 官方价格计算：原价、优惠、应付总价、配送费 | Step 4（必调） |
| `campaign-calendar` | 查询当月营销活动日历（发现限时优惠） | Step 2 |
| `create-order` | 创建订单（**仅在用户明确确认后**） | Step 5 |
| `query-order` | 查询订单详情与进度 | Step 5 |
| `order-list` | 查询近期到店/外送历史订单 | Step 3 |

> 工具说明与参数以官方 [mcd-mcp-server](https://github.com/M-China/mcd-mcp-server) 仓库为准。

---

## 3. 调用流程

### 主流程：深夜/跨时区点餐决策

```
用户："我在上海，现在凌晨 3 点，楼下只有麦当劳，帮我配一份能撑住明天上午的"
  │
  ├─▶ now-time-info                    ← 取当前时间，避免依赖系统时区假设
  │      返回：本地时间 + 时间戳
  │
  ├─▶ [询问用户所在时区]               ← 未明示则必须问，不默认 UTC+8
  │
  ├─▶ 判定时段 → 命中「深夜 23:00–05:00」分支 → 策略锁定为控油控钠优先
  │
  ├─▶ query-nearby-stores              ← 取附近门店
  ├─▶ query-meals                      ← 取该门店在售菜单（按门店区分，不套用其他门店）
  ├─▶ list-nutrition-foods             ← 取营养数据
  ├─▶ available-coupons                ← 取可领券
  │   query-my-coupons                 ← 取已有券
  │   query-store-coupons              ← 取门店限定券
  ├─▶ campaign-calendar                ← 看当月活动，补充限时优惠
  │
  ├─▶ 三档选品（A轻负担 / B均衡 / C放纵）
  │      依据：时段策略 + 营养数据横向比较 + 券可用性
  │
  ├─▶ calculate-price                  ← 官方核价，输出 原价→优惠→实付
  │
  └─▶ 输出建议 → 用户确认 → create-order（下单前必须确认）
```

### 进阶流程：跨时区代点餐

```
用户："帮我给纽约的同事点一份午餐"
  │
  ├─▶ now-time-info                    ← 换算纽约当地时段
  ├─▶ delivery-query-addresses         ← 取已存地址
  │   （地址仅在会话内使用，不落盘）
  ├─▶ delivery-query-stores            ← 该地址可配送门店
  ├─▶ query-meals                      ← 该门店菜单（≠ 自己门店菜单）
  ├─▶ list-nutrition-foods
  ├─▶ available-coupons / query-my-coupons
  └─▶ calculate-price → 确认 → create-order
```

### 调用原则

1. **实时数据优先**：餐品、价格、券、库存全部来自 MCP，不使用记忆中的静态信息。
2. **可并行则并行**：`query-meals`、`list-nutrition-foods`、`available-coupons`、`campaign-calendar` 互不依赖，并行调用以节省响应时间（注意 600 次/分钟限流）。
3. **下单前必须确认**：`create-order` / `mall-create-order` / `party-order-create` 属写操作，一律先展示订单要素并取得用户明确确认。
4. **门店差异不可忽略**：菜单、券、库存均按门店不同，代点场景必须用目标地址对应门店的数据。
5. **失败如实告知**：MCP 返回 401/403/429 时，明确告知无法获取实时数据并给出降级路径，不以静态知识冒充实时结果。

---

## 4. 业务价值

### 解决的问题

| 痛点 | 本项目的处理方式 |
|---|---|
| 深夜时段菜单被"在售"锁死，看不到最优解 | 用 `campaign-calendar` + `available-coupons` 挖掘可领券与活动，弥补夜间选项受限 |
| 跨时区用餐时段与生物钟错配 | `now-time-info` 建立时区坐标，按 8 个时段分别定义选品策略 |
| 重油重钠导致次日疲劳 | 基于 `list-nutrition-foods` 真实数据做相对比较，深夜/清晨强制轻负担档 |
| 不知道哪家券能叠加、实际要付多少 | `calculate-price` 官方核价，输出原价→优惠→实付全链路 |
| 给远端同事代点，选到对方门店没有的餐 | `delivery-query-stores` + `query-meals` 按目标地址门店取数 |

### 与现有同类方案的差异

榜单上多数项目集中在**通用省钱、预算算账、活动日历、营养计算**四个方向，横向同质化明显。本项目的差异化在于：

1. **时区是第一维度，不是附加信息**。绝大多数参赛作品假设用户与门店在同一时区；本项目把时差作为策略主轴，定义了 8 个时段的独立选品规则。
2. **时段决定策略，而非只做数据展示**。深夜档强制轻负担，禁止重油炸物——这是基于真实营养数据的相对比较得出的**约束**，不是又一张营养表格。
3. **覆盖跨时区代点餐**这一真实场景，涉及地址→门店→菜单→下单的完整链路，并明确地址只在会话内使用、不落盘。
4. **输出带核验状态**。每个数据项都标注来源工具，未通过 MCP 取得的项一律标 `[待 MCP 核验]`，杜绝幻觉数据。

---

## 5. 隐私与合规

- Token 仅通过环境变量 `${MCD_MCP_TOKEN}` 注入，不写入任何仓库文件。
- 收货地址、订单号、账号 ID 不落盘；日志与文档中一律脱敏。
- 输出内容为基于麦当劳公开营养数据的**参考搭配**，不构成医疗、营养或其他专业建议；价格与供应状态以麦当劳官方渠道实时结果为准。
- 项目为独立参赛作品，非麦当劳官方产品，不使用麦当劳商标做背书或对比。
