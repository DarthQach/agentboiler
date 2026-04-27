from fastapi import FastAPI

from app.config import settings


app = FastAPI(title="AgentBoiler", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}
