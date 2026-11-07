"""Benchmark reports and individual runtime samples

Revision ID: 0010_benchmarks
Revises: 0009_test_reports
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_benchmarks"
down_revision = "0009_test_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "benchmark_reports",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "stage_attempt_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "stage_attempts.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "report_path",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "parse_status",
            sa.String(16),
            nullable=False,
        ),
        sa.Column(
            "metric",
            sa.String(64),
        ),
        sa.Column(
            "unit",
            sa.String(32),
        ),
        sa.Column(
            "iterations",
            sa.Integer(),
        ),
        sa.Column(
            "warmups",
            sa.Integer(),
        ),
        sa.Column(
            "command_sha256",
            sa.String(64),
        ),
        sa.Column(
            "environment_key",
            sa.String(64),
        ),
        sa.Column(
            "error_message",
            sa.Text(),
        ),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "stage_attempt_id",
            name="uq_benchmark_reports_attempt",
        ),
    )

    op.create_table(
        "benchmark_samples",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "benchmark_report_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "benchmark_reports.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "iteration",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "duration_seconds",
            sa.Float(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "benchmark_report_id",
            "iteration",
            name="uq_benchmark_sample_iteration",
        ),
        sa.CheckConstraint(
            "iteration >= 1",
            name="ck_benchmark_iteration_positive",
        ),
        sa.CheckConstraint(
            "duration_seconds > 0",
            name="ck_benchmark_duration_positive",
        ),
    )

    op.create_index(
        "ix_benchmark_reports_metric_environment",
        "benchmark_reports",
        ["metric", "environment_key"],
    )


def downgrade() -> None:
    raise RuntimeError("Destructive benchmark history downgrade is blocked")
