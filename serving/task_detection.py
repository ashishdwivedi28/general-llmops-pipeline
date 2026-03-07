"""Task detection — Classify user queries and route to appropriate handlers.

This module provides a config-driven task classification system that:
  1. Reads an app config YAML (e.g. confs/app/hr_chatbot.yaml)
  2. Classifies incoming user queries by task type
  3. Returns the tools, prompt template, and model tier for that task

Supports three detection methods:
  - keyword: Fast regex/keyword matching (zero LLM cost)
  - llm: Use an LLM to classify (higher accuracy, higher cost)
  - keyword_and_llm: Try keywords first, fall back to LLM if no match
"""

from __future__ import annotations

import logging
import re
import typing as T
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


class TaskConfig(T.TypedDict, total=False):
    """Type for a single task entry in the app config."""

    description: str
    keywords: list[str]
    tools: list[str]
    prompt_template: str
    model_tier: str


class TaskDetectionResult(T.TypedDict):
    """Result of classifying a user query."""

    task_id: str
    description: str
    tools: list[str]
    prompt_template: str
    model_tier: str
    confidence: float
    method: str  # "keyword" | "llm" | "default"


class TaskDetector:
    """Classify user queries into application-specific tasks.

    Args:
        app_config_path: Path to the app YAML config.
        detection_method: "keyword", "llm", or "keyword_and_llm".
        default_task: Task ID to use when no match is found.
        llm_classifier: Optional callable(query) -> task_id for LLM-based.
    """

    def __init__(
        self,
        app_config_path: str | Path,
        detection_method: str = "keyword_and_llm",
        default_task: str = "general_qa",
        llm_classifier: T.Callable[[str], str] | None = None,
    ) -> None:
        self._config = self._load_config(app_config_path)
        self._detection_method = detection_method
        self._default_task = default_task
        self._llm_classifier = llm_classifier

        # Pre-compile keyword patterns
        task_detection = self._config.get("task_detection", {})
        self._tasks: dict[str, TaskConfig] = task_detection.get("tasks", {})
        self._keyword_patterns: dict[str, re.Pattern[str]] = {}
        for task_id, task_cfg in self._tasks.items():
            keywords = task_cfg.get("keywords", [])
            if keywords:
                pattern = "|".join(re.escape(kw) for kw in keywords)
                self._keyword_patterns[task_id] = re.compile(pattern, re.IGNORECASE)

    @property
    def app_id(self) -> str:
        """Return the application ID."""
        return self._config.get("app_id", "unknown")

    @property
    def tasks(self) -> dict[str, TaskConfig]:
        """Return all configured tasks."""
        return self._tasks

    def detect(self, query: str) -> TaskDetectionResult:
        """Classify a user query into a task.

        Args:
            query: The user's input message.

        Returns:
            TaskDetectionResult with task_id, tools, prompt, model_tier.
        """
        method = self._detection_method

        if method in ("keyword", "keyword_and_llm"):
            result = self._detect_keyword(query)
            if result is not None:
                return result

        if method in ("llm", "keyword_and_llm"):
            result = self._detect_llm(query)
            if result is not None:
                return result

        # Default task
        return self._build_result(self._default_task, 0.0, "default")

    def _detect_keyword(self, query: str) -> TaskDetectionResult | None:
        """Keyword-based detection — fast, zero cost."""
        best_task: str | None = None
        best_count = 0

        for task_id, pattern in self._keyword_patterns.items():
            matches = pattern.findall(query)
            if len(matches) > best_count:
                best_count = len(matches)
                best_task = task_id

        if best_task is not None:
            confidence = min(best_count * 0.3, 1.0)
            return self._build_result(best_task, confidence, "keyword")

        return None

    def _detect_llm(self, query: str) -> TaskDetectionResult | None:
        """LLM-based detection — higher accuracy, requires API call."""
        if self._llm_classifier is None:
            logger.warning("LLM classifier not configured — skipping LLM detection")
            return None

        try:
            task_id = self._llm_classifier(query)
            if task_id in self._tasks:
                return self._build_result(task_id, 0.85, "llm")
            else:
                logger.warning("LLM returned unknown task_id: %s", task_id)
                return None
        except Exception as exc:
            logger.warning("LLM classification failed: %s", exc)
            return None

    def _build_result(
        self, task_id: str, confidence: float, method: str
    ) -> TaskDetectionResult:
        """Build a TaskDetectionResult from a task_id."""
        task_cfg = self._tasks.get(task_id, self._tasks.get(self._default_task, {}))
        return TaskDetectionResult(
            task_id=task_id,
            description=task_cfg.get("description", ""),
            tools=task_cfg.get("tools", []),
            prompt_template=task_cfg.get("prompt_template", "system_prompt"),
            model_tier=task_cfg.get("model_tier", "primary"),
            confidence=confidence,
            method=method,
        )

    @staticmethod
    def _load_config(path: str | Path) -> dict:
        """Load the app YAML config."""
        path = Path(path)
        if not path.exists():
            logger.warning("App config not found: %s — using empty config", path)
            return {}
        with path.open() as f:
            return yaml.safe_load(f) or {}


def create_llm_classifier(
    model_name: str = "gemini-2.0-flash",
    project: str = "",
    location: str = "us-central1",
    task_descriptions: dict[str, str] | None = None,
) -> T.Callable[[str], str]:
    """Factory: build an LLM classifier function for task detection.

    Returns a callable that takes a query string and returns a task_id string.
    """

    def classify(query: str) -> str:
        from langchain_google_vertexai import ChatVertexAI

        task_list = ""
        if task_descriptions:
            for tid, desc in task_descriptions.items():
                task_list += f"- {tid}: {desc}\n"
        else:
            task_list = "- general_qa: General questions\n"

        prompt = (
            f"Classify this user query into exactly one task ID.\n\n"
            f"Available tasks:\n{task_list}\n"
            f"User query: {query}\n\n"
            f"Respond with ONLY the task_id string, nothing else."
        )

        llm = ChatVertexAI(
            model_name=model_name,
            temperature=0.0,
            project=project,
            location=location,
        )
        resp = llm.invoke(prompt)
        return resp.content.strip().lower()

    return classify
