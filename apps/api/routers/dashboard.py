"""Opt-in, loopback-only dashboard. All endpoints are read-only"""

from __future__ import annotations

import os
from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import FileResponse

from buildscope.dashboard.service import (
    DashboardNotFoundError,
    DashboardService,
)
from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.ml.live_features import PredictionContextError
from buildscope.ml.prediction import (
    PredictionError,
    TrustedFailureModel,
)
from buildscope.ml.service import FailurePredictionService

_ASSETS = Path(__file__).resolve().parents[1] / "static" / "dashboard"

_FILES = {
    "app.js": "application/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
}

_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'none'"
    ),
}


def require_local_dashboard(
    request: Request,
) -> None:
    """Reject remote clients and untrusted Host/Origin headers."""
    client = request.client.host if request.client else ""

    try:
        client_is_local = ip_address(client).is_loopback
    except ValueError:
        client_is_local = False

    if not client_is_local:
        raise HTTPException(
            status_code=403,
            detail="Loopback clients only",
        )

    host = request.headers.get("host", "")
    parsed_host = urlsplit("http://" + host).hostname

    if parsed_host not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise HTTPException(
            status_code=403,
            detail="Untrusted Host header",
        )

    origin = request.headers.get("origin")

    if origin and urlsplit(origin).netloc != host:
        raise HTTPException(
            status_code=403,
            detail="Cross-origin requests denied",
        )

    if os.getenv("BUILDSCOPE_ENABLE_LOCAL_DASHBOARD") != "1":
        raise HTTPException(
            status_code=503,
            detail="Local dashboard is disabled",
        )


router = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(require_local_dashboard)],
)


def _database_path() -> Path:
    """Resolve the locally configured SQLite database path."""
    return (
        Path(
            os.getenv(
                "BUILDSCOPE_DATABASE_PATH",
                "data/buildscope.db",
            )
        )
        .expanduser()
        .resolve(strict=True)
    )


def _service_result(operation):
    """Open a bounded database session factory per request."""
    try:
        path = _database_path()

        if not path.is_file():
            raise OSError("Database is not a file")

        engine = create_sqlite_engine(path)

    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        ) from exc

    try:
        return operation(create_session_factory(engine))

    except DashboardNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    finally:
        engine.dispose()


@router.get(
    "",
    include_in_schema=False,
)
def index() -> FileResponse:
    """Serve the local dashboard HTML."""
    return FileResponse(
        _ASSETS / "index.html",
        media_type="text/html; charset=utf-8",
        headers=_HEADERS,
    )


@router.get(
    "/assets/{filename}",
    include_in_schema=False,
)
def assets(
    filename: str,
) -> FileResponse:
    """Serve only explicitly permitted dashboard assets."""
    if filename not in _FILES:
        raise HTTPException(
            status_code=404,
            detail="Asset not found",
        )

    return FileResponse(
        _ASSETS / filename,
        media_type=_FILES[filename],
        headers=_HEADERS,
    )


@router.get("/api/overview")
def overview(
    response: Response,
    repository_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, object]:
    """Return read-only repository and pipeline overview data."""
    response.headers["Cache-Control"] = "no-store"

    return _service_result(
        lambda factory: DashboardService(factory).overview(repository_id)
    )


@router.get("/api/insights")
def insights(
    response: Response,
    repository_id: UUID,
    pipeline_name: Annotated[
        str,
        Query(min_length=1, max_length=128),
    ],
    version: Annotated[int, Query(ge=1)],
) -> dict[str, object]:
    """Return read-only insights for a saved pipeline version."""
    response.headers["Cache-Control"] = "no-store"

    return _service_result(
        lambda factory: DashboardService(factory).insights(
            repository_id,
            pipeline_name,
            version,
        )
    )


@lru_cache(maxsize=2)
def _model(
    model_dir: str,
) -> TrustedFailureModel:
    """Load an explicitly trusted local model."""
    # This path is configured by the local operator, not an HTTP client.
    return TrustedFailureModel.load(
        Path(model_dir),
        trusted=True,
    )


@router.get("/api/predictions")
def predictions(
    response: Response,
    repository_id: UUID,
    pipeline_name: Annotated[
        str,
        Query(min_length=1, max_length=128),
    ],
    version: Annotated[int, Query(ge=1)],
) -> dict[str, object]:
    """Return read-only failure predictions when explicitly enabled."""
    response.headers["Cache-Control"] = "no-store"

    if os.getenv("BUILDSCOPE_ENABLE_LOCAL_PREDICTIONS") != "1":
        return {
            "enabled": False,
            "reason": "Prediction service is disabled",
        }

    configured = os.getenv("BUILDSCOPE_TRUSTED_MODEL_DIR")

    if not configured:
        return {
            "enabled": False,
            "reason": "No model directory configured",
        }

    try:
        model = _model(str(Path(configured).expanduser().resolve(strict=True)))

    except (
        OSError,
        PredictionError,
    ) as exc:
        raise HTTPException(
            status_code=503,
            detail="Trusted model unavailable",
        ) from exc

    def run(factory):
        assessment = FailurePredictionService(
            factory,
            model,
        ).predict_pipeline(
            repository_id,
            pipeline_name,
            version,
        )

        return {
            "enabled": True,
            "model": assessment.model.schema_version,
            "model_sha256": (assessment.model.model_sha256[:12]),
            "calibrated": (assessment.model.probability_calibrated),
            "predictions": [
                {
                    "stage": item.stage_name,
                    "position": item.stage_position,
                    "score": item.failure_score,
                    "flagged": item.predicted_failure,
                }
                for item in assessment.predictions
            ],
        }

    try:
        return _service_result(run)

    except (
        PredictionContextError,
        LookupError,
    ) as exc:
        raise HTTPException(
            status_code=404,
            detail="Pipeline unavailable",
        ) from exc
