# AGV 调度系统 (AGV Scheduler)

工厂级 AGV 调度后端,基于 **FastAPI + Tortoise-ORM + MySQL**,通过 TCP 协议
对接仙工 (SEER) 控制器。Redis 预留接口但**当前阶段未启用**。

## 仙工 (SEER) 官方资料
<https://github.com/seer-robotics/Robokit_TCP_API_py> 官方 Python TCP demo (端口表 / 报文格式 / msg_type 来源) 
<https://seer-group.feishu.cn/wiki/space/7349729939798720540> 飞书 wiki
<https://cn.seer-group.com/help-center>  仙工帮助中心 

`app/connectors/seer/constants.py` 里的端口表与 msg_type 表已参照上述官方源填好;如有新的报文需要时按 API 号段加进去即可。

## 总体分层

```

HTTP / WebSocket  ← FastAPI 接入层 (api/v1)

Services 任务层 ← 业务编排

Scheduler 调度层  ← 派车/交管/充电决策

Connectors 连接层 ← 仙工/PLC/充电桩

MySQL 配置/历史/任务

Redis 暂未启用

```

## 仙工 Robokit 端口与 msg_type 段位

```
1000-1999  →  19204 (STATE)    状态查询
2000-2999  →  19205 (CTRL)     控制 (运动 / 重定位)
3000-3999  →  19206 (TASK)     任务 / 导航 (3051=gotarget)
4000-5999  →  19207 (CONFIG)   配置管理
6000-6998  →  19210 (OTHER)    杂项 (DO/IO 等)
```


## 目录速览

```
app/
├── main.py              FastAPI 入口
├── core/                配置 / 日志 / 安全 (JWT)
├── db/                  Tortoise (redis.py 未启用)
├── models/              ORM 模型 (user/agv/task/...)
├── schemas/             Pydantic 入出参
├── api/v1/endpoints/    REST 接口
├── services/            任务层 业务编排
├── scheduler/           调度层 (占位)
├── connectors/
│   ├── seer/            仙工 AGV (TCP)
│   ├── plc/             PLC (占位)
│   └── charger/         充电桩 (占位)
├── workers/             后台 asyncio (心跳轮询等,占位)
├── ws/                  WebSocket 推送 (占位)
├── web                  前端测试
└── utils/               通用工具/异常
```

## 本地启动 (Windows)

> 假设你已经本地装好了 MySQL 8。

```powershell
# 1. 建数据库
mysql -uroot -p -e "CREATE DATABASE agv_project CHARACTER SET utf8mb4;"

# 2. Python 环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3. 配置环境变量
copy .env.example .env

# 4. 初始化数据库
aerich init -t app.db.tortoise_conf.TORTOISE_ORM
aerich init-db

# 5. 写入初始账号
python -m scripts.seed_users
# 默认两个账号:
#   admin / admin123    (角色 admin, 可增删 AGV)
#   operator / op123    (角色 operator, 只读 + 测通信)


# 6. 启动开发服务
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# 浏览器打开:
#   http://localhost:8000/          自动跳到内置临时控制台 (登录页)
#   http://localhost:8000/web/      内置临时控制台 (登录 / AGV CRUD / 测通信 / 实时状态)
#   http://localhost:8000/docs      Swagger UI
#   http://localhost:8000/health    健康检查
```

## 内置临时控制台 `/web/`

只是为了在 Vue 接入前快速验证后端功能,**Vue 上线后会整体替换掉 `app/web/`**。

所有页共用 `app.js` 渲染顶栏(token / 角色 / 导航 / 退出)。token 存
`localStorage.token`,401 自动踢回登录页。

| 页面 | 说明 |
|---|---|
| `/web/` (= `index.html`) | 登录,`admin / admin123` 或 `operator / op123` |
| `/web/dashboard.html` | AGV 列表 + 测通信 + 实时状态 + 行内「下发任务」 |
| `/web/materials.html` | 3 个 tab:**零件** / **托盘类型** / **零件↔托盘 绑定** |
| `/web/ws.html` | **库位 CRUD** 表格,弹窗多选托盘类型;行内「AGV 点位」子弹窗维护每台车的 AP/pre/tp/height/liftHeight |
| `/web/call-points.html` | **呼叫点 CRUD**,弹窗 4 个业务类型 checkbox + AGV 点位子弹窗;运行状态色块跟 `current_task_id` 联动 |
| `/web/inventory.html` | 2 个 tab:**库位输入** (卡片网格,绑零件/空托/清空) 和 **放料图** (6 色色块对应 InventoryStatus);可开 2s 自动刷新 |
| `/web/tasks.html` | 任务历史 + 暂停/继续/取消,3s 自动刷新 |

所有需要鉴权的操作:operator 仅可查看 + 测通信 + 下发任务;
**admin** 才能改字典(零件/托盘类型/库位/呼叫点/库存绑定)。

## 在 Swagger 上测带鉴权的接口

后端用了 `HTTPBearer` 安全方案,所以 Swagger UI 右上角会有一个绿色 **Authorize** 按钮。

1. 先调 `POST /api/v1/auth/login` 拿到 `access_token` (响应 body 里直接复制)。
2. 点右上 **Authorize** 按钮,把 token 粘进去 —— **只填 token 本身,不要加 `Bearer ` 前缀**,Swagger 会自动拼。
3. 之后所有受保护接口都会自动带上 `Authorization: Bearer <token>`,不用每次手填 header。
4. 退出登录就点 **Logout** 或者直接关掉浏览器。

## API 速览

> 除 `POST /auth/login` 外,所有接口都需要请求头 `Authorization: Bearer <token>`。

```text
认证
  POST   /api/v1/auth/login              用户名+密码换 token (公开)
  GET    /api/v1/auth/me                 查看当前登录用户

AGV (admin 可写, operator 只读)
  GET    /api/v1/agvs                    列出全部 AGV
  POST   /api/v1/agvs                    新增 AGV          [admin]
  GET    /api/v1/agvs/{uuid}             AGV 详情
  PATCH  /api/v1/agvs/{uuid}             部分更新          [admin]
  DELETE /api/v1/agvs/{uuid}             软删 (置 inactive) [admin]
  DELETE /api/v1/agvs/{uuid}?hard=true   硬删               [admin]
  POST   /api/v1/agvs/{uuid}/ping        测通信 (发 INFO_REQ)
  GET    /api/v1/agvs/{uuid}/status      实时状态快照

任务
  POST   /api/v1/tasks                   下发任务 (落库 + 调仙工 3051)
  GET    /api/v1/tasks?agv_uuid=&status= 列出任务 (status 可多选)
  GET    /api/v1/tasks/{task_id}         任务详情
  POST   /api/v1/tasks/{task_id}/pause   暂停 (仙工 3001)
  POST   /api/v1/tasks/{task_id}/resume  继续 (仙工 3002)
  POST   /api/v1/tasks/{task_id}/cancel  取消 (仙工 3003)

任务模板 (只读,B 阶段)
  GET    /api/v1/task-templates          4 个内置模板列表
  GET    /api/v1/task-templates/{code}   按 code 查模板详情 (含 steps 数组)

呼叫调度 / 任务编排 (P4-C)
  POST   /api/v1/call-points/{uuid}/dispatch  呼叫点触发一次任务 (选车+锁库+渲染+下发)
  GET    /api/v1/tasks/{id}/detail            任务详情 (含 steps 数组,详情页用)
  POST   /api/v1/tasks/{id}/complete-early    提前完成 (cancel + 剩余step SKIPPED + 解锁)

物料字典
  GET    /api/v1/parts                   零件列表
  POST   /api/v1/parts                   新增零件                      [admin]
  GET    /api/v1/parts/{uuid}            零件详情
  PATCH  /api/v1/parts/{uuid}            更新                          [admin]
  DELETE /api/v1/parts/{uuid}            删除                          [admin]
  GET    /api/v1/parts/mappings/list     零件↔托盘类型 绑定列表
  POST   /api/v1/parts/mappings          绑定 零件↔托盘类型             [admin]
  DELETE /api/v1/parts/mappings          解绑                          [admin]
  GET    /api/v1/pallet-types            托盘类型列表
  POST   /api/v1/pallet-types            新增托盘类型                  [admin]
  GET    /api/v1/pallet-types/{uuid}     托盘类型详情
  PATCH  /api/v1/pallet-types/{uuid}     更新                          [admin]
  DELETE /api/v1/pallet-types/{uuid}     删除                          [admin]

设施字典
  GET    /api/v1/ws                      库位列表 (含 pallet_type_ids / agv_points)
  POST   /api/v1/ws                      新增库位 (同步创建 Inventory)  [admin]
  GET    /api/v1/ws/{uuid}               库位详情
  PATCH  /api/v1/ws/{uuid}               更新 (pallet_type_ids 全量替换) [admin]
  DELETE /api/v1/ws/{uuid}               删除                          [admin]
  GET    /api/v1/ws/{uuid}/agv-points    该库位的 AGV 点位列表
  PUT    /api/v1/ws/{uuid}/agv-points    新增/更新一台 AGV 在该库位的点位 [admin]
  DELETE /api/v1/ws/{uuid}/agv-points/{point_id}  删除                  [admin]

  GET    /api/v1/call-points                       呼叫点列表
  POST   /api/v1/call-points                       新增呼叫点            [admin]
  GET    /api/v1/call-points/{uuid}                呼叫点详情
  PATCH  /api/v1/call-points/{uuid}                更新                  [admin]
  DELETE /api/v1/call-points/{uuid}                删除                  [admin]
  GET    /api/v1/call-points/{uuid}/agv-points     该呼叫点的 AGV 点位
  PUT    /api/v1/call-points/{uuid}/agv-points     新增/更新点位         [admin]
  DELETE /api/v1/call-points/{uuid}/agv-points/{point_id}  删除         [admin]

库存
  GET    /api/v1/inventory               列表 / 放料图 (?status=, ?part_id=, ?ws_id=)
  GET    /api/v1/inventory/by-ws/{ws_uuid}        按库位 uuid 查
  POST   /api/v1/inventory/by-ws/{ws_uuid}/bind   绑零件 / 空托         [admin]
  POST   /api/v1/inventory/by-ws/{ws_uuid}/clear  清空库位              [admin]
  POST   /api/v1/inventory/{inv_id}/unlock        强制解锁              [admin]
```

### 任务下发参数

字段对齐仙工 3051 GOTARGET_REQ body,可在 `/web/` 控制台行内点「下发任务」直接填表:

| 字段 | 必填 | 说明 |
|---|---|---|
| `agv_uuid` | ✅ | 目标 AGV |
| `target_point` | ✅ | 目标站点 (仙工 body.id, 如 `AP1` / `LM6`) |
| `type` | | 语义分类: NAVIGATE / JACK_LOAD / FORK_LOAD / ... (仅归档) |
| `source_id` | | 起点站点 (可选) |
| `operation` | | 动作: `JackLoad` / `JackUnload` / `ForkLoad` / `ForkUnload` / `RollerLoad` / `RollerUnload` / `HookLoad` / `HookUnload` / `JackHeight` (必须在 Roboshop Pro 给站点配好执行对象,否则 AGV 拒收) |
| `angle` | | 到点朝向 rad,缺省走站点设置 |
| `extra_args` | | 其它仙工 3051 接受的字段 (`script_args` 等),原样透传 |

后端流程:

```
POST /tasks ─┬─ 落库 Task(status=INIT)
             ├─ SeerAPI.dispatch_task(body)  → 仙工 3051
             ├─ 成功  → status=RUNNING, started_at=now
             └─ 失败  → status=FAILED,  error_msg, finished_at
```

### 后台任务状态轮询

`app/workers/task_poller.py` 每 2s 扫一次 status 为 RUNNING/PAUSED 的任务,按 AGV
聚合并行调仙工 1020 `task_req`,按 `task_id` 对账后更新数据库 status。

- AGV 不可达跳过本轮,不改 status (避免误判失败)
- 终态(COMPLETED/FAILED/CANCELED)不再回退
- 仙工 task_status → 本地 status 的映射在 `task_service.apply_seer_state()` 里,
  按官方 RBKTaskStatus 枚举对齐;后续实测有偏差再调

启动 / 关闭由 `app/main.py` lifespan 控制,无需手动管理。

### curl 速查

```bash
# 登录拿 token
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# 之后所有请求带 token
TOKEN="<上一步返回的 access_token>"

# 添加一台 AGV
curl -X POST http://localhost:8000/api/v1/agvs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"uuid":"AGV-001","name":"测试1号","ip":"192.168.1.100"}'

# 测通信
curl -X POST http://localhost:8000/api/v1/agvs/AGV-001/ping \
  -H "Authorization: Bearer $TOKEN"

# 实时状态
curl http://localhost:8000/api/v1/agvs/AGV-001/status \
  -H "Authorization: Bearer $TOKEN"

# 下发任务 (纯导航)
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agv_uuid":"AGV-001","target_point":"AP1"}'

# 下发任务 (顶升取货)
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agv_uuid":"AGV-001","target_point":"AP1","type":2,"operation":"JackLoad"}'

# 暂停 / 继续 / 取消
curl -X POST http://localhost:8000/api/v1/tasks/1/pause  -H "Authorization: Bearer $TOKEN"
curl -X POST http://localhost:8000/api/v1/tasks/1/resume -H "Authorization: Bearer $TOKEN"
curl -X POST http://localhost:8000/api/v1/tasks/1/cancel -H "Authorization: Bearer $TOKEN"
```

### 数据库迁移 (Aerich)

每次改 `app/models/*.py` 后,在本机:

```powershell
.\.venv\Scripts\aerich.exe migrate --name <短描述>
.\.venv\Scripts\aerich.exe upgrade
```

已知历史变更:`task_dispatch` 这次迁移把 TaskStatus 枚举值整体后移
(在 `RUNNING(1)` 后插入 `PAUSED(2)`),迁移文件里已加 UPDATE 把旧值平移,
确保旧数据语义不丢。如果你跑 `aerich upgrade` 失败要回滚,用 `aerich downgrade`。

## 调度域数据模型 (P1+P2+P3)

> 这一轮把"物料 / 设施 / 库存"字典先建起来,
> P4 阶段才会加 task_template + 呼叫接口(根据零件查库存→选 AGV→下发)。

**物料域**

| 表 | 说明 |
|---|---|
| `part` | 零件字典,`code` = PartSN |
| `pallet_type` | 托盘类型/规格,挂 `.shelf` 识别文件 + `agv_mode` (该托盘只能由哪种 AGV 搬) |
| `part_pallet_mapping` | 零件↔托盘类型 多对多 |

**设施域** (PLC 字段全部移除,需要时再回填)

| 表 | 说明 |
|---|---|
| `ws` | 库位。`allow_empty_pallet / allow_full_material / allow_defect` 三个 bool 代替原 wsTypeChild 数组 |
| `ws_agv_point` | 库位在每台 AGV 上的导航点位 (ap / pre / tp / height / lift_height) |
| `ws_pallet_type` | 库位↔托盘类型 多对多 |
| `call_point` | 呼叫点。运行时 `run_status` + `current_task_id` 直接挂在主表,不另建工位状态表 |
| `call_point_agv_point` | 呼叫点在每台 AGV 上的导航点位 |
| `call_point_business_type` | 呼叫点↔支持的业务类型(4 种)多对多 |

**业务类型枚举** (`BusinessType`,4 个值 = 4 种任务模板入口):

| 值 | 名称 | 含义 |
|---|---|---|
| 1 | `SEND_EMPTY_TO_WS` | 呼叫点 → 库位 送空托 |
| 2 | `FETCH_MATERIAL_TO_CP` | 库位 → 呼叫点 送料 |
| 3 | `FETCH_EMPTY_TO_CP` | 库位 → 呼叫点 送空托 |
| 4 | `SEND_MATERIAL_TO_WS` | 呼叫点 → 库位 送料 |

**库存域**

| 表 | 说明 |
|---|---|
| `inventory` | 与 `ws` 1:1,记 `part` / `pallet_type` / `status`,带 `is_locked` 防并发 |

`InventoryStatus`: `0=DISABLED, 1=EMPTY_SLOT, 2=EMPTY_PALLET, 3=FULL_MATERIAL, 4=PENDING_ALLOC, 5=IN_USE`
(对应前端放料图 7 色块)

> 创建 WS 时会自动建一行 Inventory(EMPTY_SLOT),无需手工初始化。

## 任务业务模型 (P4-B 数据层)

> 本轮只完成"数据 + 模板"。下一轮 (P4-C) 接呼叫调度 + 详情页 UI。

**Task 表新增业务字段** (全部 nullable,向后兼容旧手动任务):

| 字段 | 说明 |
|---|---|
| `business_type` | 4 种业务枚举之一;手动 ad-hoc 任务可为空 |
| `template_id` | 套用的模板;手动任务为空 |
| `call_point_id` | 触发的呼叫点 |
| `from_ws_id` / `to_ws_id` | 起点/终点库位 |
| `part_id` / `pallet_type_id` | 零件 / 托盘 |
| `inventory_id` | 锁定的库存行(任务结束清锁) |
| `current_step_no` | 当前推进到第几步 |
| `duration_sec` | 完成后由 finished-started 计算 |
| `description` | 详情页直观描述 |

**AGV 表新增运行时字段** (调度选车 + 心跳缓存):

| 字段 | 说明 |
|---|---|
| `run_state` | `UNKNOWN/IDLE/RUNNING/PAUSED/CHARGING/LOW_BATTERY/OFFLINE/ERROR` |
| `battery_level` | 0-100,心跳 worker 从仙工 1004 拉取并缓存 |
| `low_battery_threshold` | 默认 20,低于此值不予派工 |
| `current_task_uuid` | 当前在执行的 task.uuid |
| `last_status_at` | 最近一次状态拉取时间 |

**TaskTemplate 表** (4 条种子数据, `business_type` 唯一索引):

| code | name | business_type |
|---|---|---|
| `SEND_EMPTY_TO_WS` | 呼叫点送空托至库位 | 1 |
| `FETCH_MATERIAL_TO_CP` | 库位送物料至呼叫点 | 2 |
| `FETCH_EMPTY_TO_CP` | 库位送空托至呼叫点 | 3 |
| `SEND_MATERIAL_TO_WS` | 呼叫点送物料至库位 | 4 |

`steps` JSON 数组,所有 4 个模板都用同一个 6 步骨架(顶升车):

| step | module | operation | point_role |
|---|---|---|---|
| 0 | command | JackUnload | SELF (自检) |
| 1 | path | pathNavigation | preStart |
| 2 | command | JackLoad | start |
| 3 | path | pathNavigation | preEnd |
| 4 | command | JackUnload | end |
| 5 | request | isEmpty | SELF (验证) |

下一轮 C 阶段渲染时按 business_type 把 start/end 占位符翻译成真实站点:

```
SEND_EMPTY_TO_WS / SEND_MATERIAL_TO_WS:  start=call_point  end=to_ws
FETCH_MATERIAL_TO_CP / FETCH_EMPTY_TO_CP: start=from_ws     end=call_point
```

**TaskStep 表** (一对多挂在 task 下):

| 字段 | 说明 |
|---|---|
| `task_id / step_no` | 唯一索引 |
| `module / operation / class_name / point_role / point_value / input` | 模板渲染后的最终值 |
| `status` | `PENDING / RUNNING / DONE / FAILED / SKIPPED` (跳过用于"提前完成") |
| `is_ok / error_msg / started_at / finished_at / duration_ms` | 执行结果 |

**初始化模板** (幂等):

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_task_templates           # 已存在跳过
.\.venv\Scripts\python.exe -m scripts.seed_task_templates --reset   # 强制覆盖
```

## 呼叫调度 (P4-C)

**整体流程**:

```
POST /call-points/{uuid}/dispatch
  ├─ 校验呼叫点支持该业务
  ├─ 按业务类型解析 part/pallet/source_ws/target_ws/inventory 上下文
  ├─ 选 AGV: is_active + run_state ∈ {IDLE, UNKNOWN} + battery>=阈值 + current_task IS NULL
  │    (可用 prefer_agv_uuid 显式指定)
  ├─ 锁库存 (inventory.is_locked=True, locked_by_task_id=task.id)
  ├─ 创建 Task + 6 个 TaskStep (PENDING),step0 自检本地标 DONE
  ├─ 下发"取段" 3051 (target=start point_value, op=JackLoad)
  │     成功 → status=RUNNING, current_step_no=2, step1/2 RUNNING
  │     失败 → _abort: task FAILED + 所有未完成 step FAILED + 解锁 + AGV/CP 释放
  └─ ↓ 等仙工 1020 task_status=4 (completed)
```

**task_poller 推进**:

```
task 完成上报 (seer_status=4)
  ├─ task.current_step_no=2 → advance_task → 下发"放段" 3051 (target=end, op=JackUnload)
  │                            step1/2 DONE, step3/4 RUNNING, current_step_no=4
  ├─ task.current_step_no=4 → advance_task → step3/4/5 DONE → finalize_task
  └─ finalize_task: status=COMPLETED + 解锁 + 推进 inventory 状态机 + AGV/CP 释放
```

**4 种业务完成后的库存状态机**:

| 业务 | 操作对象 | 状态转移 |
|---|---|---|
| SEND_EMPTY_TO_WS | to_ws | EMPTY_SLOT → EMPTY_PALLET (写入 pallet_type) |
| SEND_MATERIAL_TO_WS | to_ws | EMPTY_SLOT → FULL_MATERIAL (写入 part + pallet_type) |
| FETCH_EMPTY_TO_CP | from_ws | EMPTY_PALLET → EMPTY_SLOT |
| FETCH_MATERIAL_TO_CP | from_ws | FULL_MATERIAL → EMPTY_SLOT |

**提前完成 (`POST /tasks/{id}/complete-early`)** 语义:

- 调仙工 3003 cancel 让 AGV 停车 (仙工失败不阻塞收尾)
- 所有 PENDING/RUNNING 的 step → SKIPPED (保留已 DONE 的)
- task → COMPLETED + finalize (注意:不推进 inventory 状态机,只解锁)
- AGV / CP 释放

**心跳 worker (`agv_status_poller`)**:

- 每 5s 并发拉所有 active AGV 的 1007 BATTERY + 1002 RUN + 1020 TASK
- 写回 `AGV.battery_level / run_state / current_task_uuid / last_status_at`
- 3 个请求全失败 → run_state=OFFLINE,current_task_uuid 清空
- 本地有 RUNNING/PAUSED 任务时,run_state 锁定为对应状态(避免误判 IDLE)
- 空闲 + battery < `low_battery_threshold` 时降级为 LOW_BATTERY (调度选车跳过)

**已知局限** (留给下一轮):

1. **没有自动选库位** —— SEND 类必须 user 指定 `target_ws_uuid`,FETCH 类只做了"找第一个匹配"
2. **没有行级锁** —— 高并发场景两个 dispatch 可能选中同一台 AGV (P5 加 SELECT FOR UPDATE)
3. **单步取消** —— complete_early 是"跳全部剩余 step",没有"只取消第 N 步"
4. **没有详情页 UI** —— 任务详情接口已有 (`/tasks/{id}/detail`),Vue 接入前 Web 端待补

## 当前进度

- [x] 项目骨架 / 仙工协议层 / TCP 客户端 / 连接池
- [x] 登录 + JWT + RBAC (admin & operator)
- [x] AGV 增删改查 + 测通信 + 实时状态
- [x] 内置临时控制台 (`/web/`) — 登录 + AGV 列表 + 行内任务下发
- [x] 任务下发 (`POST /tasks`) + pause/resume/cancel + 后台轮询对账
- [x] 任务历史页 (`/web/tasks.html`,3 秒自动刷新)
- [x] 协议 + 客户端集成测试 (10/10 通过)
- [x] **P1 物料字典: part / pallet_type / part_pallet_mapping**
- [x] **P2 设施字典: ws / call_point + AGV 点位 + 多对多绑定**
- [x] **P3 库存: inventory (随 WS 自动建,支持手动绑零件/空托)**
- [x] **P4-B 任务业务数据模型: Task 业务字段 + AGV 运行时字段 + TaskTemplate + TaskStep + 4 模板种子**
- [x] **P4-C 呼叫调度后端: 心跳 worker + 分段下发(取段/放段) + advance + finalize + complete_early + 库存状态机回写**
- [ ] **P4-D 任务详情页前端: 步骤列表 / 取消单步 / 提前完成 / 实时进度刷新**
- [ ] P5 多车并发优化: 行级锁 / SELECT FOR UPDATE / 多 worker 实例去重
- [ ] WebSocket 实时状态推送
- [ ] 调度层增强 (自动选库位 / 交管 / 自动充电)
- [ ] 前端 Vue (会替换掉 `app/web/`)
