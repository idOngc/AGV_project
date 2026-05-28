"""
初始化账号脚本 —— 把这里列出的账号写入 user 表。

用法 (在项目根目录,激活了虚拟环境之后):
    python -m scripts.seed_users

特性:
  - 已存在的用户名不会被覆盖,只会被跳过
  - 加 --reset 参数可强制重置已存在用户的密码和角色 (调试用)
  - 密码用 bcrypt 哈希后入库

修改账号:直接编辑下面的 SEED_USERS 列表后重跑即可。
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from tortoise import Tortoise

from app.core.logging import setup_logging
from app.core.security import hash_password
from app.db.tortoise_conf import TORTOISE_ORM
from app.models.user import Role, User

# 初始账号清单 
# 字段:username, password, role
SEED_USERS: list[dict] = [
    {"username": "admin",    "password": "admin123", "role": Role.ADMIN},
    {"username": "operator", "password": "op123",    "role": Role.OPERATOR},
]


setup_logging()
log = logging.getLogger("seed_users")


async def _seed(reset: bool) -> None:
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        for u in SEED_USERS:
            exist = await User.filter(username=u["username"]).first()
            if exist and not reset:
                log.info("跳过已存在用户: %s", u["username"])
                continue
            if exist and reset:
                exist.password_hash = hash_password(u["password"])
                exist.role = u["role"]
                exist.is_active = True
                await exist.save()
                log.info("重置用户: %s (role=%s)", u["username"], u["role"].value)
            else:
                await User.create(
                    username=u["username"],
                    password_hash=hash_password(u["password"]),
                    role=u["role"],
                    is_active=True,
                )
                log.info("新建用户: %s (role=%s)", u["username"], u["role"].value)
    finally:
        await Tortoise.close_connections()


def main() -> None:
    p = argparse.ArgumentParser(description="种子用户账号")
    p.add_argument("--reset", action="store_true", help="覆盖已存在用户的密码和角色")
    args = p.parse_args()
    asyncio.run(_seed(reset=args.reset))


if __name__ == "__main__":
    main()
