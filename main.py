import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from routers import all_in_one, jobs, settings
from safe_logging import log_safe_exception

logger = logging.getLogger(__name__)

ALLOWED_CORS_ORIGINS = (
    "https://copypilot.app",
    "https://all-in-one.copypilot.app",
    "https://copypilot-platform-mohyeects-projects.vercel.app",
)


def _cors_headers(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin")
    if origin not in ALLOWED_CORS_ORIGINS:
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Vary": "Origin",
    }


app = FastAPI(
    title="All in One Copy API",
    description="Full page production — meta, FAQs, and full page copy in a single pipeline",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_CORS_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(all_in_one.router, prefix="/api/all-in-one", tags=["all-in-one"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    """Return a safe 500 while retaining CORS for exact approved origins."""
    log_safe_exception(
        logger,
        "aio.http.unhandled",
        exc,
        method=request.method,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
        headers=_cors_headers(request),
    )
