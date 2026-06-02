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
