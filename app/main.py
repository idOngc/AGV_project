"""
FastAPI 入口 —— 负责 lifespan(启动/关闭 Tortoise)、路由挂载、全局异常处理。

Redis: 当前阶段暂未启用
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from tortoise import Tortoise

from app.api.v1.router import api_router
from app.connectors.seer.manager import seer_manager
from app.core.config import settings
from app.core.logging import setup_logging
# REDIS: from app.db.redis import close_redis, init_redis
from app.db.tortoise_conf import TORTOISE_ORM
from app.utils.exceptions import register_exception_handlers
from app.workers.agv_status_poller import agv_status_poller
from app.workers.task_poller import task_poller

setup_logging()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """生命周期:启动时连接 MySQL,关闭时断开。"""
    log.info("=== %s starting (env=%s) ===", settings.APP_NAME, settings.APP_ENV)
    await Tortoise.init(config=TORTOISE_ORM)
    log.info("Tortoise connected.")
    # REDIS: await init_redis()

    # 注: SEER 连接走懒连接策略,这里不需要主动 init_from_db。
    await task_poller.start()
    await agv_status_poller.start()

    try:
        yield
    finally:
        log.info("=== shutting down ===")
        await agv_status_poller.stop()
        await task_poller.stop()
        await seer_manager.close_all()
        # REDIS: await close_redis()
        await Tortoise.close_connections()


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
)

register_exception_handlers(app)
app.include_router(api_router)

# 测试
@app.get("/health", tags=["meta"], summary="健康检查")
async def health() -> dict:
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}


# 静态前端(临时页面,将来 Vue 上线后移除)
_WEB_DIR = Path(__file__).parent / "web" / "static"
if _WEB_DIR.is_dir():

    class _NoCacheStaticFiles(StaticFiles):
        """开发期 staticfiles:HTML/JS/CSS 一律不缓存,避免改前端后忘了硬刷新。
        Vue 上线后这一层会被替换掉,生产环境自然会按构建出来的 hash 文件缓存。"""

        async def get_response(self, path: str, scope):  # type: ignore[override]
            resp: Response = await super().get_response(path, scope)
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
            return resp

    app.mount(
        "/web",
        _NoCacheStaticFiles(directory=str(_WEB_DIR), html=True),
        name="web",
    )

    @app.get("/", include_in_schema=False)
    async def _root_redirect() -> RedirectResponse:
        """根路径直接跳到登录页,方便浏览器 http://host:port/ 直达。"""
        return RedirectResponse(url="/web/")

