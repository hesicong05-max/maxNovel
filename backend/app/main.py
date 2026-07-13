"""FastAPI main application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chapters, community, export, outline, projects, settings, worldview
from app.config import settings as app_settings
from app.core.settings_store import load_settings
from app.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title=app_settings.APP_NAME,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=app_settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(projects.router)
app.include_router(worldview.router)
app.include_router(outline.router)
app.include_router(chapters.router)
app.include_router(export.router)
app.include_router(settings.router)
app.include_router(community.router)


@app.get("/api/health")
async def health_check():
    s = load_settings()
    return {
        "status": "ok",
        "app": app_settings.APP_NAME,
        "llm_configured": bool(s.get("api_key", "")),
    }
