"""FastAPI 앱 엔트리포인트.

실행: bizplan-mvp/ 디렉토리에서
  uvicorn backend.main:app --reload --port 8000
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

_log_dir = Path(__file__).resolve().parent.parent / "logs"
_log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(_log_dir / "app.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import programs, sessions, interview, generation, results, matching, dev
from services.session_store import cleanup_old_sessions


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_old_sessions()
    async def _daily_cleanup():
        while True:
            await asyncio.sleep(86400)
            cleanup_old_sessions()
    asyncio.create_task(_daily_cleanup())
    yield


app = FastAPI(title="BizPlan AI API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(programs.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(interview.router, prefix="/api")
app.include_router(generation.router, prefix="/api")
app.include_router(results.router, prefix="/api")
app.include_router(matching.router, prefix="/api")
app.include_router(dev.router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok"}
