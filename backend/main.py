"""FastAPI 앱 엔트리포인트.

실행: backend/ 디렉토리에서
  uvicorn main:app --reload --port 8000
"""
import sys
from pathlib import Path

_backend = Path(__file__).resolve().parent   # bizplan-mvp/backend/
_root    = _backend.parent                   # bizplan-mvp/
for _p in [str(_backend), str(_root)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

_log_dir = Path(__file__).resolve().parent / "logs"
_log_dir.mkdir(exist_ok=True, parents=True)

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

from auth import require_auth_secret
from routers import auth, programs, sessions, interview, generation, results, matching, dev
from services.session_store import cleanup_old_sessions
from services.user_store import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시크릿·계정 DB는 기동 시 검증한다 — 런타임에 처음 발견되면 가입/로그인이 통째로 실패한다
    require_auth_secret()
    init_db()
    cleanup_old_sessions()

    async def _daily_cleanup():
        while True:
            await asyncio.sleep(86400)
            cleanup_old_sessions()

    async def _notion_sync_loop():
        """노션 피드백 캐시를 시작 시 1회 + 주기적으로 자동 동기화.

        자격증명이 없으면 루프를 돌리지 않고 커밋된 캐시를 그대로 사용한다.
        동기화 실패(노션 장애·네트워크 등)는 로그만 남기고 기존 캐시를 유지한다.
        """
        if not os.getenv("NOTION_API_KEY"):
            logging.info("[노션 동기화] NOTION_API_KEY 미설정 — 자동 동기화 비활성(기존 캐시 사용)")
            return
        from scripts.sync_notion_feedback import sync as notion_sync
        import core.notion_feedback as notion_feedback
        interval = int(os.getenv("NOTION_SYNC_INTERVAL_SEC", "21600"))  # 기본 6시간
        while True:
            try:
                await asyncio.to_thread(notion_sync)   # blocking 호출은 스레드로
                notion_feedback.reset_cache()          # 메모리 캐시 무효화 → 즉시 반영
                logging.info("[노션 동기화] 완료 — 피드백 캐시 갱신됨")
            except SystemExit as e:
                logging.warning("[노션 동기화] 건너뜀: %s", e)
            except Exception as e:  # noqa: BLE001
                logging.warning("[노션 동기화] 실패(기존 캐시 유지): %s", e)
            await asyncio.sleep(interval)

    asyncio.create_task(_daily_cleanup())
    asyncio.create_task(_notion_sync_loop())
    yield


app = FastAPI(title="BizPlan AI API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
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
