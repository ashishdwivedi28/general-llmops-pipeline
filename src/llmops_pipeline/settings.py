"""Application settings — Pydantic discriminated union dispatches to the correct Job class."""

import pydantic as pdt
import pydantic_settings as pdts

from llmops_pipeline import pipelines


class Settings(pdts.BaseSettings, strict=True, frozen=True, extra="allow"):
    """Base settings class."""


class MainSettings(Settings):
    """Main settings — the `job.KIND` field selects which pipeline job to run.

    Example YAML:
        job:
          KIND: FeatureEngineeringJob
          embedding_model: text-embedding-004
          ...
    """

    job: pipelines.JobKind = pdt.Field(..., discriminator="KIND")
