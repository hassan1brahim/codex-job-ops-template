"""Runtime configuration for the Job Description Fetcher module."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# Defaults to a folder inside this module (not the caller's cwd), so output
# stays co-located with the module that produced it regardless of where
# `python -m module_1_jd_fetcher` is run from.
MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = Path(os.environ.get("JOB_APP_AGENT_DATA_DIR", str(MODULE_DIR / ".job_app_agent")))


@dataclass(frozen=True)
class AppConfig:
    """Resolved paths and environment-backed settings used by the app."""

    data_dir: Path = DEFAULT_DATA_DIR
    jobs_dir: Path = Path(os.environ.get("JOB_APP_AGENT_JOBS_DIR", DEFAULT_DATA_DIR / "jobs"))

    @classmethod
    def from_env(cls) -> "AppConfig":
        data_dir = Path(os.environ.get("JOB_APP_AGENT_DATA_DIR", str(MODULE_DIR / ".job_app_agent")))
        return cls(
            data_dir=data_dir,
            jobs_dir=Path(os.environ.get("JOB_APP_AGENT_JOBS_DIR", data_dir / "jobs")),
        )
