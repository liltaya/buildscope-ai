"""Opt-in, loopback-only, read-only failure prediction API"""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from apps.api.security import require_local_request
from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.git.history import GitMetadataError
from buildscope.ml.live_features import PredictionContextError
from buildscope.ml.prediction import (
    PredictionError,
    load_configured_model,
)
from buildscope.ml.service import FailurePredictionService

router = APIRouter(
    prefix="/predictions",
    tags=["predictions"],
    dependencies=[Depends(require_local_request)],
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


@router.post(
    "/stages",
    response_model=PipelinePredictionResponse,
)
def predict_stages(
    request: PredictPipelineRequest,
) -> PipelinePredictionResponse:
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
        model = load_configured_model(Path(model_path))

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

    except GitMetadataError as exc:
        raise HTTPException(
            status_code=503,
            detail="Repository Git metadata unavailable",
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
