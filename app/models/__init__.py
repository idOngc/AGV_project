"""
显示导出所有模型,方便别处 `from app.models import AGV, Task` 使用。

注意:Aerich 是通过读取 `tortoise_conf.TORTOISE_ORM` 里的模块路径发现模型的,
      不是读这里,所以新增表记得同步去 tortoise_conf.py 加路径。
"""

from app.models.agv import AGV
from app.models.facility import (
    CallPoint,
    CallPointAgvPoint,
    CallPointBusinessTypeBinding,
    CallPointPalletTypeBinding,
    WS,
    WSAgvPoint,
    WSPalletTypeBinding,
)
from app.models.inventory import Inventory
from app.models.material import PalletType, Part, PartPalletMapping
from app.models.task import Task, TaskStep, TaskTemplate
from app.models.user import Role, User

__all__ = [
    "User",
    "Role",
    "AGV",
    "Task",
    "TaskTemplate",
    "TaskStep",
    "Part",
    "PalletType",
    "PartPalletMapping",
    "WS",
    "WSAgvPoint",
    "WSPalletTypeBinding",
    "CallPoint",
    "CallPointAgvPoint",
    "CallPointBusinessTypeBinding",
    "CallPointPalletTypeBinding",
    "Inventory",
]
