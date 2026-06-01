"""
业务异常基类 + FastAPI 全局异常处理器。

在 main.py 里调 register_exception_handlers(app)。
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """业务层可预期异常的基类。"""

    code: int = 1000
    msg: str = "app error"
    http_status: int = 400

    def __init__(self, msg: str | None = None, *, code: int | None = None, http_status: int | None = None):
        super().__init__(msg or self.msg)
        self.msg = msg or self.msg
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status


class AGVNotFound(AppError):
    code = 2001
    msg = "AGV not found"
    http_status = 404


class AGVOffline(AppError):
    code = 2002
    msg = "AGV offline / unreachable"
    http_status = 503


# ---------- 物料域 ----------
class PartNotFound(AppError):
    code = 3001
    msg = "零件不存在"
    http_status = 404


class PartConflict(AppError):
    code = 3002
    msg = "零件编码已存在"
    http_status = 409


class PalletTypeNotFound(AppError):
    code = 3003
    msg = "托盘类型不存在"
    http_status = 404


class PalletTypeConflict(AppError):
    code = 3004
    msg = "托盘类型编码已存在"
    http_status = 409


# ---------- 设施域 ----------
class WSNotFound(AppError):
    code = 4001
    msg = "库位不存在"
    http_status = 404


class WSConflict(AppError):
    code = 4002
    msg = "库位编码已存在"
    http_status = 409


class CallPointNotFound(AppError):
    code = 4003
    msg = "呼叫点不存在"
    http_status = 404


class CallPointConflict(AppError):
    code = 4004
    msg = "呼叫点编码已存在"
    http_status = 409


# ---------- 库存域 ----------
class InventoryNotFound(AppError):
    code = 5001
    msg = "库存记录不存在"
    http_status = 404


class InventoryLocked(AppError):
    code = 5002
    msg = "库存被任务锁定,操作被拒"
    http_status = 409


class InventoryStateError(AppError):
    code = 5003
    msg = "库存状态不允许此操作"
    http_status = 409


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "msg": exc.msg, "data": None},
        )
