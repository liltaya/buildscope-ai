"""Read-only stage-failure inference with model provenance"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from buildscope.database.session import SessionFactory
from buildscope.ml.live_features import (
    build_live_stage_features,
)
from buildscope.ml.prediction import (
    ModelInfo,
    StagePrediction,
    TrustedFailureModel,
)


@dataclass(frozen=True, slots=True)
class PipelineRiskAssessment:
    repository_id: UUID
    pipeline_name: str
    pipeline_version: int
    model: ModelInfo
    predictions: tuple[StagePrediction, ...]


class FailurePredictionService:
    def __init__(
        self,
        factory: SessionFactory,
        model: TrustedFailureModel,
    ) -> None:
        self._factory = factory
        self._model = model

    def predict_pipeline(
        self,
        repository_id: UUID,
        pipeline_name: str,
        version: int,
    ) -> PipelineRiskAssessment:
        stages = build_live_stage_features(
            self._factory,
            repository_id,
            pipeline_name,
            version,
        )

        predictions = tuple(
            self._model.predict_one(
                stage.stage_name,
                stage.stage_position,
                stage.values,
            )
            for stage in stages
        )

        return PipelineRiskAssessment(
            repository_id=repository_id,
            pipeline_name=pipeline_name,
            pipeline_version=version,
            model=self._model.info,
            predictions=predictions,
        )
