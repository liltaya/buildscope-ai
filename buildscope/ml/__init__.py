"""BuildScope machine-learning training and prediction"""

from buildscope.ml.prediction import (
    ModelInfo,
    PredictionError,
    StagePrediction,
    TrustedFailureModel,
)
from buildscope.ml.service import (
    FailurePredictionService,
    PipelineRiskAssessment,
)
from buildscope.ml.training import (
    ModelTrainingError,
    TrainingResult,
    train_failure_classifier,
)

__all__ = [
    "FailurePredictionService",
    "ModelInfo",
    "ModelTrainingError",
    "PipelineRiskAssessment",
    "PredictionError",
    "StagePrediction",
    "TrainingResult",
    "TrustedFailureModel",
    "train_failure_classifier",
]
