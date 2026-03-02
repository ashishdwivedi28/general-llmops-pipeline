"""Base Job class — all pipeline jobs inherit from this.

Provides:
- Pydantic model for config validation (strict, frozen, immutable)
- Context manager pattern: __enter__ starts services, __exit__ stops them
- Abstract run() method each job must implement
- KIND discriminator field for Pydantic discriminated union dispatch
"""

from __future__ import annotations

import abc
import types as TS
import typing as T

import pydantic as pdt

from llmops_pipeline.io.services import LoggerService, VertexAIService

Locals = T.Dict[str, T.Any]


class Job(abc.ABC, pdt.BaseModel, strict=True, frozen=True):
    """Base class for all pipeline jobs.

    Usage:
        with job as runner:
            runner.run()

    Services are started on __enter__ and stopped on __exit__.
    """

    KIND: str

    logger_service: LoggerService = LoggerService()
    vertex_ai_service: VertexAIService = VertexAIService()

    def __enter__(self) -> T.Self:
        """Start all services."""
        self.logger_service.start()
        logger = self.logger_service.logger()
        logger.info("[START] Job: {} | KIND: {}", self.__class__.__name__, self.KIND)
        self.vertex_ai_service.start()
        return self

    def __exit__(
        self,
        exc_type: T.Type[BaseException] | None,
        exc_value: BaseException | None,
        exc_traceback: TS.TracebackType | None,
    ) -> T.Literal[False]:
        """Stop all services. Always propagates exceptions."""
        logger = self.logger_service.logger()
        self.vertex_ai_service.stop()
        logger.info("[STOP] Job: {}", self.__class__.__name__)
        self.logger_service.stop()
        return False

    @abc.abstractmethod
    def run(self) -> Locals:
        """Execute the job logic. Returns a dict of local variables for inspection."""
