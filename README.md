# AGV 调度系统 (AGV Scheduler)

工厂级 AGV 调度后端,基于 **FastAPI + Tortoise-ORM + MySQL**,通过 TCP 协议
对接仙工 (SEER) 控制器。Redis 预留接口但**当前阶段未启用**。

## 仙工 (SEER) 官方资料

| 来源 | 用途 |
|---|---|
| <https://github.com/seer-robotics/Robokit_TCP_API_py> | 官方 Python TCP demo (端口表 / 报文格式 / msg_type 来源) |
| <https://seer-group.feishu.cn/wiki/space/7349729939798720540> | 飞书 wiki (需账号登录) |
| <https://cn.seer-group.com/help-center> | 仙工帮助中心 |

`app/connectors/seer/constants.py` 里的端口表与 msg_type 表已**严格按上述官方源**填好；
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
4. **配置/历史落 MySQL**；实时状态目前也走 MySQL,后续接入 Redis 时再切。
5. 一台 AGV 多个长连接 (按端口) + 每条连接一个 `asyncio.Task` 收包；
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

# 5. 启动开发服务
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 浏览器打开:
#   http://localhost:8000/docs    Swagger UI
#   http://localhost:8000/health  健康检查
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
- [ ] 仙工 TCP 客户端 (`client.py`) + 高层 API (`api.py`)
- [ ] AGV / Task 模型与 CRUD 接口
- [ ] 任务下发接口 `POST /api/v1/tasks`
- [ ] WebSocket 实时状态推送
- [ ] 调度层 (派车 / 交管 / 自动充电)
- [ ] 前端 Vue
