"""AI analysis and human review service.

This service receives evidence, stores analyses and records review decisions. It has no
endpoint, credential or code path that can run a build, edit code or change infrastructure.
"""

import hmac
import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ai import analyzer
from ai.config import Settings
from ai.schemas import EvidencePackage, ReviewRequest, to_jsonable
from ai.store import Store

MAX_BODY_BYTES = 512 * 1024
STATIC_DIR = Path(__file__).parent / "static"
ACCEPT_MEANING = (
    "Accept means the reviewer accepts this recommendation for consideration or later "
    "manual implementation. It executes nothing."
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ai.service")


def create_app(settings: Settings | None = None, provider=None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = Store(settings.database_path)
    app = FastAPI(title="AI Failure Analysis & Human Review", version="1.0.0")

    def require_ingest_token(x_ingest_token: str = Header(default="")) -> None:
        # Fails closed: with no token configured, nothing can be ingested.
        if not settings.ingest_token or not hmac.compare_digest(
            x_ingest_token.encode(), settings.ingest_token.encode()
        ):
            raise HTTPException(status_code=401, detail="invalid ingest token")

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return JSONResponse(status_code=413, content={"detail": "request body too large"})
        return await call_next(request)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/ready")
    def ready() -> dict:
        store.ping()
        return {
            "status": "ready",
            "provider": settings.provider,
            "model": settings.llm_model if settings.provider == "llm" else None,
            "fallback_to_mock": settings.fallback_to_mock,
        }

    @app.post("/api/analyses", dependencies=[Depends(require_ingest_token)])
    def submit_evidence(package: EvidencePackage) -> dict:
        result = analyzer.analyze(package, settings, provider=provider)
        store.save_analysis(analyzer.redact_package(package), result)
        return to_jsonable(result)

    @app.get("/api/analyses")
    def list_analyses() -> list[dict]:
        return store.list_analyses()

    @app.get("/api/analyses/{build_id}")
    def get_analysis(build_id: str) -> dict:
        record = store.get(build_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown build")
        return record

    @app.post("/api/analyses/{build_id}/recommendations/{recommendation_id}/review")
    def review(build_id: str, recommendation_id: str, request: ReviewRequest) -> dict:
        record = store.get(build_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown build")
        known = {action["id"] for action in record["analysis"]["recommended_actions"]}
        if recommendation_id not in known:
            raise HTTPException(status_code=404, detail="unknown recommendation")
        event = store.record_review(
            build_id, recommendation_id, request.decision, request.reviewer, request.comment
        )
        log.info(
            "review build=%s recommendation=%s decision=%s reviewer=%s action_executed=false",
            build_id,
            recommendation_id,
            request.decision,
            request.reviewer,
        )
        return {"event": to_jsonable(event), "meaning": ACCEPT_MEANING}

    @app.get("/api/audit")
    def audit(build_id: str | None = None) -> list[dict]:
        return [to_jsonable(event) for event in store.audit(build_id)]

    @app.get("/api/stats")
    def stats() -> dict:
        return store.stats()

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
