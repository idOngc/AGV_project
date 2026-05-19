"""
显示导出所有模型,方便别处 `from app.models import AGV, Task` 使用。

注意：Aerich 是通过读取 `tortoise_conf.TORTOISE_ORM` 里的模块路径发现模型的,
      不是读这里,所以新增表记得同步去 tortoise_conf.py 加路径。
"""

from app.models.agv import AGV
from app.models.task import Task
from app.models.user import Role, User

__all__ = ["User", "Role", "AGV", "Task"]
