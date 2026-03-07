"""Deployment — Canary release with smoke tests and automatic rollback.

Provides:
  - SmokeTest: validates a newly deployed Cloud Run revision actually works
  - CanaryManager: gradually shifts traffic from old → new revision
  - Rollback on failure: reverts traffic split if smoke test fails
"""

from __future__ import annotations

import logging
import time
import typing as T

logger = logging.getLogger(__name__)


class SmokeTestResult(T.TypedDict):
    """Result of a smoke test suite."""

    passed: bool
    checks: list[dict[str, T.Any]]
    duration_ms: float


class SmokeTest:
    """Run a battery of HTTP checks against a Cloud Run service URL.

    Args:
        base_url: Cloud Run service URL (e.g. https://llmops-agent-dev-xxx.run.app).
        timeout_s: Timeout in seconds per request.
    """

    def __init__(self, base_url: str, timeout_s: int = 30) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s

    def run(self) -> SmokeTestResult:
        """Execute all smoke tests and return results."""
        import requests

        start = time.monotonic()
        checks: list[dict[str, T.Any]] = []

        # Test 1: Health endpoint
        checks.append(self._check_endpoint("GET", "/health", 200))

        # Test 2: Ready endpoint
        checks.append(self._check_endpoint("GET", "/ready", 200))

        # Test 3: Chat endpoint (basic round-trip)
        checks.append(
            self._check_endpoint(
                "POST",
                "/chat",
                200,
                json={"query": "Hello, smoke test!", "session_id": "smoke-test"},
            )
        )

        # Test 4: Manifest endpoint
        checks.append(self._check_endpoint("GET", "/manifest", 200))

        passed = all(c.get("passed", False) for c in checks)
        duration = (time.monotonic() - start) * 1000

        return SmokeTestResult(passed=passed, checks=checks, duration_ms=duration)

    def _check_endpoint(
        self,
        method: str,
        path: str,
        expected_status: int,
        json: dict | None = None,
    ) -> dict[str, T.Any]:
        """Check a single endpoint."""
        import requests

        url = f"{self._base_url}{path}"
        try:
            resp = requests.request(method, url, json=json, timeout=self._timeout)
            passed = resp.status_code == expected_status
            return {
                "endpoint": path,
                "method": method,
                "status_code": resp.status_code,
                "expected": expected_status,
                "passed": passed,
                "body_preview": resp.text[:200] if not passed else "",
            }
        except Exception as exc:
            return {
                "endpoint": path,
                "method": method,
                "passed": False,
                "error": str(exc),
            }


class CanaryManager:
    """Manage canary traffic splits on Cloud Run.

    Uses the gcloud CLI or Cloud Run Admin API to gradually shift traffic.

    Args:
        project: GCP project ID.
        region: GCP region.
        service_name: Cloud Run service name.
        canary_steps: Traffic percentages for canary stages (e.g. [10, 50, 100]).
        wait_between_steps_s: Seconds to wait between canary steps.
    """

    def __init__(
        self,
        project: str,
        region: str,
        service_name: str,
        canary_steps: list[int] | None = None,
        wait_between_steps_s: int = 300,
    ) -> None:
        self._project = project
        self._region = region
        self._service = service_name
        self._steps = canary_steps or [10, 50, 100]
        self._wait = wait_between_steps_s

    def deploy_canary(
        self,
        new_revision: str,
        old_revision: str = "LATEST",
        smoke_test_url: str = "",
    ) -> dict[str, T.Any]:
        """Execute a canary deployment with smoke tests between steps.

        Args:
            new_revision: Name of the new Cloud Run revision.
            old_revision: Name of the current stable revision.
            smoke_test_url: URL to smoke-test the new revision.

        Returns:
            dict with deployment status, final_traffic, and smoke_test_results.
        """
        results: dict[str, T.Any] = {
            "status": "in_progress",
            "steps_completed": [],
            "smoke_tests": [],
        }

        for pct in self._steps:
            logger.info("Canary step: %d%% traffic to %s", pct, new_revision)

            # Set traffic split
            success = self._set_traffic_split(new_revision, pct, old_revision)
            if not success:
                results["status"] = "failed_traffic_split"
                return results

            results["steps_completed"].append(pct)

            # Run smoke test at each step
            if smoke_test_url:
                time.sleep(10)  # Let the new traffic settle
                smoker = SmokeTest(smoke_test_url)
                smoke_result = smoker.run()
                results["smoke_tests"].append(
                    {"step_pct": pct, "result": smoke_result}
                )

                if not smoke_result["passed"]:
                    logger.warning(
                        "Smoke test failed at %d%% — rolling back to %s",
                        pct,
                        old_revision,
                    )
                    self._rollback(old_revision)
                    results["status"] = "rolled_back"
                    results["rollback_reason"] = f"smoke_test_failed_at_{pct}pct"
                    return results

            # Wait before next step (skip for 100%)
            if pct < 100:
                logger.info("Waiting %ds before next canary step", self._wait)
                time.sleep(self._wait)

        results["status"] = "success"
        results["final_traffic"] = {new_revision: 100}
        return results

    def _set_traffic_split(
        self, revision: str, percentage: int, old_revision: str
    ) -> bool:
        """Set traffic split via Cloud Run Admin API."""
        try:
            from google.cloud import run_v2

            client = run_v2.ServicesClient()
            service_path = (
                f"projects/{self._project}/locations/{self._region}"
                f"/services/{self._service}"
            )

            service = client.get_service(name=service_path)

            # Update traffic to include the canary split
            service.traffic = [
                run_v2.TrafficTarget(
                    revision=revision,
                    percent=percentage,
                    type_=run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION,
                ),
            ]
            if percentage < 100:
                service.traffic.append(
                    run_v2.TrafficTarget(
                        percent=100 - percentage,
                        type_=run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST,
                    ),
                )

            client.update_service(service=service)
            logger.info("Traffic split set: %s=%d%%", revision, percentage)
            return True
        except Exception as exc:
            logger.error("Failed to set traffic split: %s", exc)
            return False

    def _rollback(self, stable_revision: str) -> None:
        """Roll back all traffic to the stable revision."""
        logger.warning("ROLLBACK: Setting 100%% traffic to stable revision")
        self._set_traffic_split(stable_revision, 100, "")
