from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import all_in_one, jobs, settings

app = FastAPI(
    title="All in One Copy API",
    description="Full page production — meta, FAQs, and full page copy in a single pipeline",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://copypilot.app",
        "https://all-in-one.copypilot.app",
    ],
    allow_origin_regex=r"https://copypilot-platform(?:-[a-z0-9-]+)?-mohyeects-projects\.vercel\.app",
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


from fastapi import Request
from fastapi.responses import JSONResponse


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    """Inject CORS headers on unhandled 500s.
    Railway EU edge strips CORS from 500 responses, causing misleading
    CORS errors in DevTools that mask the real server error."""
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
        headers={
            "Access-Control-Allow-Origin": request.headers.get("origin", "*"),
            "Access-Control-Allow-Credentials": "true",
        },
    )
