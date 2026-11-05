"""Opt-in, loopback-only, read-only failure prediction API"""

from __future__ import annotations

import os

from dataclasses import asdict
from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

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


router = APIRouter(
    prefix="/predictions",
    tags=["predictions"],
)


class PredictPipelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: UUID
    pipeline_name: str = Field(
        min_length=1,
        max_length=128,
    )
    version: int = Field(
        ge=1,
        strict=True,
    )


class StageRiskResponse(BaseModel):
    stage_name: str
    stage_position: int
    failure_score: float
    predicted_failure: bool


class PipelinePredictionResponse(BaseModel):
    repository_id: UUID
    pipeline_name: str
    pipeline_version: int

    model_schema_version: str
    model_sha256: str
    model_trained_at_utc: str

    decision_threshold: float
    probability_calibrated: bool

    predictions: list[StageRiskResponse]


@lru_cache(maxsize=1)
def _trusted_local_model(
    path: str,
) -> TrustedFailureModel:
    return TrustedFailureModel.load(
        Path(path),
        trusted=True,
    )


@router.post(
    "/stages",
    response_model=PipelinePredictionResponse,
)
def predict_stages(
    request: PredictPipelineRequest,
    http_request: Request,
) -> PipelinePredictionResponse:
    client_host = http_request.client.host if http_request.client else ""

    try:
        is_loopback = ip_address(client_host).is_loopback

    except ValueError:
        is_loopback = False

    if not is_loopback:
        raise HTTPException(
            status_code=403,
            detail="Local loopback clients only",
        )

    if os.environ.get("BUILDSCOPE_ENABLE_LOCAL_PREDICTIONS") != "1":
        raise HTTPException(
            status_code=503,
            detail="Local predictions are disabled",
        )

    model_path = os.environ.get("BUILDSCOPE_TRUSTED_MODEL_DIR")

    database_path = os.environ.get(
        "BUILDSCOPE_DATABASE_PATH",
        "data/buildscope.db",
    )

    if not model_path:
        raise HTTPException(
            status_code=503,
            detail="Trusted model directory is not configured",
        )

    try:
        model = _trusted_local_model(
            str(Path(model_path).expanduser().resolve(strict=True))
        )

        engine = create_sqlite_engine(
            Path(database_path).expanduser().resolve(strict=True)
        )

    except (OSError, PredictionError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Prediction service unavailable",
        ) from exc

    try:
        service = FailurePredictionService(
            create_session_factory(engine),
            model,
        )

        result = service.predict_pipeline(
            request.repository_id,
            request.pipeline_name,
            request.version,
        )

    except (
        PredictionContextError,
        LookupError,
    ) as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    finally:
        engine.dispose()

    return PipelinePredictionResponse(
        repository_id=result.repository_id,
        pipeline_name=result.pipeline_name,
        pipeline_version=result.pipeline_version,
        model_schema_version=result.model.schema_version,
        model_sha256=result.model.model_sha256,
        model_trained_at_utc=result.model.trained_at_utc,
        decision_threshold=result.model.decision_threshold,
        probability_calibrated=result.model.probability_calibrated,
        predictions=[StageRiskResponse(**asdict(row)) for row in result.predictions],
    )
