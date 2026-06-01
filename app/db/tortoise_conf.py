"""
Tortoise-ORM 配置 —— 同时供应用启动和 Aerich 迁移工具使用。

每新增一个模型文件,要把它的模块路径加入 `models` 列表,否则 Aerich 不会发现。
`aerich.models` 是 Aerich 自身需要的迁移记录表。
"""

from app.core.config import settings

TORTOISE_ORM: dict = {
    "connections": {
        "default": settings.mysql_url,
    },
    "apps": {
        "models": {
            "models": [
                "app.models.user",
                "app.models.agv",
                "app.models.task",
                "app.models.material",
                "app.models.facility",
                "app.models.inventory",
                "aerich.models",
            ],
            "default_connection": "default",
        },
    },
    "use_tz": False,
    "timezone": "Asia/Shanghai",
}
