"""Benchmark collection and Git-aware performance regression analysis"""

from buildscope.benchmarks.analysis import (
    BenchmarkAnalysisService,
    BenchmarkComparison,
    SlowdownAssessment,
)
from buildscope.benchmarks.reports import (
    BenchmarkReportError,
    ParsedBenchmarkReport,
    parse_benchmark_report,
    prepare_benchmark_command,
)
from buildscope.benchmarks.service import (
    BenchmarkReportDetails,
    BenchmarkService,
)

__all__ = [
    "BenchmarkAnalysisService",
    "BenchmarkComparison",
    "BenchmarkReportDetails",
    "BenchmarkReportError",
    "BenchmarkService",
    "ParsedBenchmarkReport",
    "SlowdownAssessment",
    "parse_benchmark_report",
    "prepare_benchmark_command",
]
