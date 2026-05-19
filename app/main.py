"""
FastAPI 入口 —— 负责 lifespan(启动/关闭 Tortoise)、路由挂载、全局异常处理。

启动:
    uvicorn app.main:app --reload

Redis: 当前阶段暂未启用。文件保留在 app/db/redis.py,将来要启用只需把下面
标了 `# REDIS:` 的注释取消,并在 requirements.txt 解开 redis 依赖即可。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from tortoise import Tortoise

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import setup_logging
# REDIS: from app.db.redis import close_redis, init_redis
from app.db.tortoise_conf import TORTOISE_ORM
from app.utils.exceptions import register_exception_handlers

setup_logging()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """生命周期：启动时连接 MySQL,关闭时断开。"""
    log.info("=== %s starting (env=%s) ===", settings.APP_NAME, settings.APP_ENV)
    await Tortoise.init(config=TORTOISE_ORM)
    log.info("Tortoise connected.")
    # REDIS: await init_redis()

    # TODO: 后续在这里启动 seer_manager.init_from_db() / status_poller 等

    try:
        yield
    finally:
        log.info("=== shutting down ===")
        # TODO: await seer_manager.close_all()
        # REDIS: await close_redis()
        await Tortoise.close_connections()


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
)

register_exception_handlers(app)
app.include_router(api_router)


@app.get("/health", tags=["meta"], summary="健康检查")
async def health() -> dict:
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}
