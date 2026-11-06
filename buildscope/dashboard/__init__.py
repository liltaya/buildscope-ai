"""Local dashboard read-only query layer"""

from buildscope.dashboard.service import (
    DashboardNotFoundError,
    DashboardService,
)

__all__ = [
    "DashboardNotFoundError",
    "DashboardService",
]
