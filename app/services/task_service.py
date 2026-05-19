"""
任务业务编排 —— 下一步补完。

拟定主入口:
    async def create_and_dispatch(payload: TaskCreateIn) -> Task:
        1. 检查 AGV 存在 / 启用
        2. INSERT task (status=INIT)
        3. 调 connectors.seer.api.navigate(...) 下发
        4. 成功 → status=RUNNING,失败 → status=FAILED+error_msg
        5. 返回 Task
"""
