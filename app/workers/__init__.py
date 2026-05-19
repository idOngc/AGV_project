"""
后台 asyncio 任务 (lifespan 启动/收尾)。

拟发存任务:
  - status_poller.py   轮询 AGV 状态 (心跳 + 1Hz 位姿)
  - task_monitor.py    轮询任务进度,状态流转
"""
