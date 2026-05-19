"""
用户 / 角色 最简模型：admin / operator 两种角色。

表设计故意设为平面,不上 casbin / 多表权限,后期有需要再演进。
"""

from enum import Enum

from tortoise import fields
from tortoise.models import Model


class Role(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"


class User(Model):
    id = fields.IntField(pk=True)
    username = fields.CharField(max_length=64, unique=True, description="登录名")
    password_hash = fields.CharField(max_length=255, description="bcrypt 哈希")
    role = fields.CharEnumField(Role, default=Role.OPERATOR, description="角色")
    is_active = fields.BooleanField(default=True)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "user"

    def __str__(self) -> str:
        return f"<User {self.username} ({self.role})>"
