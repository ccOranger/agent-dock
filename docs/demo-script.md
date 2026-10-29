# 演示脚本与取景清单（v0.1）

> 用途：README 首屏 GIF 的录制台词 + 发布物料截图的取景清单。
> 台词取自 `backend/tests/fixtures/eval/customer_service.json` 的**已验证用例**（W4 实测 36/39 通过），
> 照读即可复现，不需要临场想问法。
> 关联：[开源发布检查清单](./open-source-release.md)、[评测与回归](./evaluation.md)、[快速开始](./quickstart.md)。

## 1. 前置准备

```bash
cp backend/.env.example .env      # 至少填 LLM_* / EMBED_* / JWT_SECRET
# 首次启动要演示数据，必须在 .env 里写 SEED_DEMO=true
docker compose up -d --build
docker compose ps                 # postgres / qdrant / redis / backend / web 均 healthy
```

| 项 | 值 |
|---|---|
| 客户对话端 | `http://localhost:8080/chat?scene=customer_service` |
| 运营台 | `http://localhost:8080/admin/` |
| 后台账号 | `admin / Admin@123`（admin）、`service01 / Agent@123`（agent） |
| 客户账号 | `alice / Alice@123` |
| 演示场景 | `customer_service`（LLM 路由到 `complaint_handler` / `knowledge_qa` / `order_service`） |
| 演示知识库 | `cs_default`（产品 FAQ）、`cs_order`（订单物流）、`cs_after_sales`（售后投诉） |
| 示例订单 | `SO20240001` 已发货、`SO20240003` 已签收、`SO20240004` 处理中 |

**录制前先清一次库**（否则镜头里全是评测留下的 `ev-<时间戳>-*` 访客）：

```bash
docker compose down -v && docker compose up -d --build   # 全新数据卷，种子重新导入
```

只清访客、不想重建时，至少确认后台「咨询管理」列表里没有 `ev-` 开头的会话。

## 2. GIF 分镜：对话端主流程

目标文件：`docs/assets/screenshots/01-chat-multi-intent.gif`，1280×800，**4 帧共 13 秒**（前 3 帧各 3s，末帧 4s，512KB）。
下面五镜中前四镜是 GIF 的四帧，第 5 镜是后台链路页的单独截图。

浏览器窗口固定在 1280×800（Chrome 无痕 → F12 设备工具栏 → Responsive 1280×800 → 截图/录制时隐藏滚动条）。
先用 `alice / Alice@123` 登录，进入 `/chat?scene=customer_service`，确认右上角显示「场景 customer_service」。

| 镜 | 帧 | 输入内容 | 预期画面 | 展示的能力 |
|---|---|---|---|---|
| 1 | 第 1 帧 | `帮我查一下订单 SO20240001 的物流到哪了` | 首字很快出现，回答里有运单号与「运输中」状态 | 场景路由 → `order_service` → 工具 `query_logistics` 真实回填 |
| 2 | 第 2 帧 | `查一下订单 SO20240003 的物流到哪了，另外我要投诉你们客服态度差` | 顶部出现两个意图标签，先答物流再答投诉，投诉自动建工单 | **多意图拆解 + 按主次串行应答** |
| 3 | 第 3 帧 | `订单号是 SO20240001` | 直接续到上一轮 Agent，把订单号补进工单（不再重新路由） | 追问续接 + 跨轮补录（`setup` 轮语义） |
| 4 | 第 4 帧 | `我不想再等了，直接给我转人工` | 出现「已为您转接人工客服」与工单升级提示 | 升级人工（`handover` 事件） |
| 5 | 单独截图 | 切到 `http://localhost:8080/admin/traces`，点第一条最新链路 | 展开 spans：路由 / 意图 / 工具 / 模型分发段与耗时 | **全链路追踪**，一轮对话说得清哪一步 |

注意事项：

- **第 4 镜必须是最后一轮对话**：转人工后机器人不再自动应答，继续追问会截不到内容。
- 第 3 镜依赖第 2 镜的投诉上下文，**不要打乱顺序**；如果第 2 镜没建单，回到第 2 镜重录。
- 第 5 镜从对话页切过来时，按时间倒序取第一条即可；若要精确定位，把回答末尾的 `trace_id` 粘进「会话 ID / 链路 ID」框。
- 工具没被调用（模型直接背政策）就重录一次，别用没调工具的回答凑数——这一镜要证明的正是「不是纯提示词」。

## 3. 截图清单

统一规格：**宽 1280、PNG、单张 ≤ 500KB**；同一轮录制内保持同一主题与同一浏览器窗口。
保存到 `docs/assets/screenshots/`，文件名直接用下表，README 与文档按名引用。

| 文件 | 页面 | 前置状态 | 要证明什么 |
|---|---|---|---|
| `02-admin-traces.png` | `/admin/traces` → 点开第 5 镜那一轮 | 已有 3–5 轮对话 | 路由/LLM/工具/RAG/记忆的 spans、耗时、`trace_id`，且意图命中依据可见 |
| `03-admin-scenes.png` | `/admin/scenes` | `customer_service` 已启用、3 个 Agent 按序绑定 | 一个 `/chat/stream` 靠 `scene_code` 分场景；场景是配置出来的，不是写死的 |
| `04-admin-knowledge.png` | `/admin/knowledge` | 3 个演示文档 `ready`，跑过一次检索测试 | 多命名空间 + 分块浏览 + 检索测试命中片段 |
| `05-admin-agents.png` | `/admin/agents` | 打开 `order_service` 编辑态 | 路由关键词/典型问法、绑定工具、按 Agent 指定模型 |
| `06-admin-analytics.png`（待补） | `/admin/analytics` | 至少 7 天、每天 ≥6 轮对话，采样率 1 或 0.2 | 全量三率与链路三率分组、采样角标、每日请求量趋势折线图；原 `06-admin-usage.png`（`/admin/usage`）已随页面下线废弃 |
| `07-widget-embed.png` | `/embed-demo.html` | 场景接入已开「开放匿名」 | 一行 script 嵌入第三方页面，气泡 + iframe 样式隔离 |
| `08-admin-license.png`（可选） | `/admin/license` | 未放授权文件（默认社区版） | 降级不阻断：只 5 个功能位关闭，对话与评测完整保留 |

README 只用 **1 / 2 / 3** 三张（首屏 1 张大图 + 2 张并排），其余放发布物料，不堆首屏。

### 打码与合规

- 任何页面出现 `sk-` 开头的 Key、真实域名、真实邮箱/手机号 → 换演示数据或裁掉，**不允许打马赛克后仍可复原**。
- 后台右上角账号固定用 `admin`，别露公司内部用户名。
- 授权页显示「社区版 / 未配置授权」是正常状态，截图里正好用来说明降级不阻断。

## 4. 录制与压缩参数

| 参数 | 值 |
|---|---|
| 分辨率 | 1280×800（窗口与录制区域一致，别录进任务栏） |
| 像素 | 截图按 2× 像素抓取后降采样到 1280×800，文字更锐利 |
| 时长 | 当前产物 4 帧 /13 秒；录屏方案控制在 ≤40 秒 |
| 体积 | GIF ≤ 5MB（当前 512KB）；PNG 单张 ≤ 500KB |
| 工具 | 分帧截图用浏览器自动化（本仓库未引入依赖），录屏用 ScreenToGif |

当前 GIF 的构成：`01-frame1..4.png` 为四帧原料，每帧停留 3 秒（末帧 4 秒）、256 色、循环播放；
**帧原料不入库**，只保留合成后的 GIF，重录时重新生成即可。

导出后压缩（ScreenToGif 导出时选 256 色 + 合理帧延迟；仍超标就删掉等待帧）：

```powershell
# 单张截图压到 500KB 以内（pngquant 需先安装 choco install pngquant）
pngquant --quality=65-80 --ext .png --force docs\assets\screenshots\*.png
```

## 5. 验收

```powershell
# 1) 文件齐不齐、体积超没超
Get-ChildItem docs\assets\screenshots -File | ForEach-Object {
  "{0,-28} {1,7:N0} KB" -f $_.Name, ($_.Length / 1KB)
}

# 2) README 引用的图是否都存在（4 条命中且逐个能打开）
Select-String -Path README.md -Pattern 'assets/screenshots'

# 3) 发布门禁（含必备文件与仓库卫生）
$env:PYTHONIOENCODING="utf-8"
python scripts/release.py check
```

`docs/assets/screenshots/` 空目录不会被 git 跟踪，本文件与 `.gitkeep` 保证目录随仓库存在；
图片到位后直接提交，不需要改任何文档路径。
