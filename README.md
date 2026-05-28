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

- 登录页 (`/web/`) → 用 `admin / admin123` 或 `operator / op123` 登录。
- AGV 控制台 (`/web/dashboard.html`):
  - admin 可以「新增 / 删除 AGV」;operator 只能看 + 测通信 + 下发任务。
  - 「测通信」: 调 `POST /api/v1/agvs/{uuid}/ping`,在线显示延迟 ms,失败显示原因。
  - 「实时状态」: `GET /api/v1/agvs/{uuid}/status`,弹窗 JSON 快照。
  - 「下发任务」: 弹窗表单(目标点 / 类型 / operation / source_id / angle / extra_args),
    确认后调 `POST /api/v1/tasks` 落库 + 真发到 AGV。
- 任务历史 (`/web/tasks.html`):
  - 列出所有任务,3 秒自动刷新(可关掉)。
  - 按 AGV / 状态过滤。
  - 每行「暂停 / 继续 / 取消」按钮,按状态机自动 enable/disable。
  - 点 `#ID` 看完整 JSON 详情(含最近一次仙工 task_req 应答)。
- token 存在浏览器 `localStorage.token` 里;401 时自动踢回登录页。

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

## 当前进度

- [x] 项目骨架 / 仙工协议层 / TCP 客户端 / 连接池
- [x] 登录 + JWT + RBAC (admin & operator)
- [x] AGV 增删改查 + 测通信 + 实时状态
- [x] 内置临时控制台 (`/web/`) — 登录 + AGV 列表 + 行内任务下发
- [x] 任务下发 (`POST /tasks`) + pause/resume/cancel + 后台轮询对账
- [x] 任务历史页 (`/web/tasks.html`,3 秒自动刷新)
- [x] 协议 + 客户端集成测试 (10/10 通过)
- [ ] 呼叫点 PLC 接入 (生成"送空车 / 运物料"任务)
- [ ] 库位 WMS / 地图数据接入
- [ ] WebSocket 实时状态推送
- [ ] 调度层 (派车 / 交管 / 自动充电)
- [ ] 前端 Vue (会替换掉 `app/web/`)
