"""Import every ORM model so Base.metadata describes the full schema."""

from __future__ import annotations

from sqlalchemy import MetaData

from buildscope.benchmarks.models import (
    BenchmarkReportRecord,
    BenchmarkSampleRecord,
)
from buildscope.database.base import Base
from buildscope.pipelines.attempt_models import StageAttemptRecord
from buildscope.pipelines.models import PipelineDefinitionRecord
from buildscope.pipelines.run_models import (
    PipelineRunRecord,
    StageRunRecord,
)
from buildscope.repositories.models import Repository
from buildscope.testing.models import (
    TestCaseRecord,
    TestReportRecord,
)
from buildscope.workers.models import (
    WorkerHeartbeatRecord,
    WorkerJobRecord,
)

MODELS = (
    Repository,
    PipelineDefinitionRecord,
    PipelineRunRecord,
    StageRunRecord,
    StageAttemptRecord,
    WorkerJobRecord,
    WorkerHeartbeatRecord,
    TestReportRecord,
    TestCaseRecord,
    BenchmarkReportRecord,
    BenchmarkSampleRecord,
)


def full_metadata() -> MetaData:
    """Return metadata with every BuildScope table registered."""
    return Base.metadata
