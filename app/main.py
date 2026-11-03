import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import admin, auth, internal, leaderboard, practice, sessions, users
from app.core import db
from app.core.config import settings

logging.basicConfig(level=logging.INFO)
STATIC = Path(__file__).resolve().parent.parent / "static"

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://accounts.google.com/gsi/client",
    "style-src 'self' https://fonts.googleapis.com https://accounts.google.com/gsi/style",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data: https://*.googleusercontent.com",
    "frame-src https://accounts.google.com/gsi/",
    "connect-src 'self' https://accounts.google.com/gsi/",
    "base-uri 'none'", "object-src 'none'", "form-action 'self'", "frame-ancestors 'none'",
])


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.connect()
    yield
    await db.close()


app = FastAPI(
    title="Brain Monkey", lifespan=lifespan,
    docs_url=None if settings.env == "production" else "/docs",
    redoc_url=None, openapi_url=None if settings.env == "production" else "/openapi.json",
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.cookie_secure:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith(("/api/", "/internal/")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(sessions.router)
app.include_router(practice.router)
app.include_router(leaderboard.router)
app.include_router(admin.router)
app.include_router(internal.router)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC / "index.html")
