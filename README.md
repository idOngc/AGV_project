# AGV 调度系统 (AGV Scheduler)

工厂级 AGV 调度后端,基于 **FastAPI + Tortoise-ORM + MySQL**,通过 TCP 协议
对接仙工 (SEER) 控制器。Redis 预留接口但**当前阶段未启用**。

## 仙工 (SEER) 官方资料

| 来源 | 用途 |
|---|---|
| <https://github.com/seer-robotics/Robokit_TCP_API_py> | 官方 Python TCP demo (端口表 / 报文格式 / msg_type 来源) |
| <https://seer-group.feishu.cn/wiki/space/7349729939798720540> | 飞书 wiki (需账号登录) |
| <https://cn.seer-group.com/help-center> | 仙工帮助中心 |

`app/connectors/seer/constants.py` 里的端口表与 msg_type 表已**严格按上述官方源**填好;
有新的报文需要时按 API 号段加进去即可。

## 总体分层

```
┌──────────────────────────────────────────────┐
│  HTTP / WebSocket  ← FastAPI 接入层 (api/v1) │
├──────────────────────────────────────────────┤
│  Services 任务层    ← 业务编排                │
├──────────────────────────────────────────────┤
│  Scheduler 调度层   ← 派车/交管/充电决策      │
├──────────────────────────────────────────────┤
│  Connectors 连接层  ← 仙工/PLC/充电桩 (★ 唯一  │
│                       允许出现硬件细节)        │
└──────────────────────────────────────────────┘
        ↓                          ↓
   ┌─────────┐                ┌─────────┐
   │  MySQL  │ 配置/历史/任务  │  Redis  │ 暂未启用 (规划中)
   └─────────┘                └─────────┘
```

## 仙工 Robokit 端口与 msg_type 段位

```
1000-1999  →  19204 (STATE)    状态查询
2000-2999  →  19205 (CTRL)     控制 (运动 / 重定位)
3000-3999  →  19206 (TASK)     任务 / 导航 (3051=gotarget)
4000-5999  →  19207 (CONFIG)   配置管理
5100-5199  →  19208 (KERNEL)   daemon 文件传输
6000-6998  →  19210 (OTHER)    杂项 (DO/IO 等)
```

> ⚠️ 连接层路由由 `connectors.seer.constants.port_for(msg_type)` 统一裁决,
> 上层不要硬编码端口号。

## 关键约束 (始终遵守)

1. **硬件细节只允许出现在 `app/connectors/`** —— 字节、msg_type、socket、modbus 等。
   向上只暴露语义化方法 (`navigate()`, `get_status()`, `cancel_task()`)。
2. `app/services/` 只编排业务流程,不直接 carry 字节。
3. `app/scheduler/` 做全局决策,输出"派给谁",由 services 调 connectors 落地。
4. **配置/历史落 MySQL**;实时状态目前也走 MySQL,后续接入 Redis 时再切。
5. 一台 AGV 多个长连接 (按端口) + 每条连接一个 `asyncio.Task` 收包;
   `req_id → Future` 字典做请求/响应配对。

## 目录速览

```
app/
├── main.py              FastAPI 入口
├── core/                配置 / 日志 / 安全 (JWT)
├── db/                  Tortoise (redis.py 已保留但未启用)
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
└── utils/               通用工具/异常
```

## 本地启动 (Windows)

> 假设你已经本地装好了 MySQL 8。

```powershell
# 1. 建数据库
mysql -uroot -p -e "CREATE DATABASE agv_scheduler CHARACTER SET utf8mb4;"

# 2. Python 环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3. 配置环境变量
copy .env.example .env
# 编辑 .env 填入 MySQL 实际地址与密码 (REDIS_* 留空也行)

# 4. 初始化数据库 (第一次运行)
aerich init -t app.db.tortoise_conf.TORTOISE_ORM
aerich init-db

# 5. 写入初始账号 (首次运行)
python -m scripts.seed_users
# 默认两个账号:
#   admin / admin123    (角色 admin, 可增删 AGV)
#   operator / op123    (角色 operator, 只读 + 测通信)
# 改密码: 编辑 scripts/seed_users.py 里的 SEED_USERS 后:
#   python -m scripts.seed_users --reset

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
- 控制台 (`/web/dashboard.html`):
  - admin 可以「新增 / 删除 AGV」;operator 只能看 + 测通信。
  - 每行「测通信」按钮调 `POST /api/v1/agvs/{uuid}/ping`,在线时显示延迟。
  - 「实时状态」按钮调 `GET /api/v1/agvs/{uuid}/status`,弹窗里展示 JSON 快照。
  - 「全部测连接」会并发对所有启用的 AGV 各发一次 ping。
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

任务 (待实装,当前 501)
  POST   /api/v1/tasks                   下发任务
  GET    /api/v1/tasks/{task_id}         查询任务
```

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
```

## 启用 Redis (将来需要再做)

```
1) requirements.txt 取消 `redis>=5.0,<6.0` 那行注释
   pip install -r requirements.txt
2) app/main.py 把所有 `# REDIS:` 注释去掉
3) 业务代码 from app.db.redis import get_redis 直接用
```

## 当前进度

- [x] 项目骨架搭建
- [x] 仙工协议层迁移 (`app/connectors/seer/protocol.py`)
- [x] 仙工端口表 / msg_type 表对齐官方源 (`constants.py`)
- [x] Redis 暂时屏蔽
- [x] 仙工 TCP 客户端 (`client.py`) — 单端口异步,req_id↔Future 配对,自动重连
- [x] 仙工高层 API (`api.py`) — `ping/get_battery/get_location/snapshot/navigate` 等
- [x] 连接池 (`manager.py`) — 懒连接,AGV 配置变更自动失效
- [x] 登录 / JWT / RBAC (admin & operator)
- [x] AGV 增删改查 + `/ping` 测通信 + `/status` 实时状态
- [x] 协议层 + 客户端集成测试 (10/10 通过)
- [x] 内置临时控制台 (`/web/`) — 登录 + AGV 列表 + 测通信 + 实时状态
- [ ] 任务下发接口 `POST /api/v1/tasks` (实装中)
- [ ] WebSocket 实时状态推送
- [ ] 调度层 (派车 / 交管 / 自动充电)
- [ ] 前端 Vue (会替换掉 `app/web/`)



# 虚拟环境构建
python3.12 -m venv .env
source .env/bin/activate
deactivate