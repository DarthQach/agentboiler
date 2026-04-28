from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers.billing import router as billing_router
from app.routers.chat import router as chat_router
from app.routers.usage import router as usage_router


app = FastAPI(title="AgentBoiler", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(billing_router)
app.include_router(chat_router)
app.include_router(usage_router)


@app.exception_handler(HTTPException)
async def plan_limit_exception_handler(request: Request, exc: HTTPException):
    if (
        exc.status_code == 402
        and isinstance(exc.detail, dict)
        and "error" in exc.detail
        and "message" in exc.detail
    ):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    return await http_exception_handler(request, exc)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}
