"""Orchestrateur : execute la suite de tests et calcule les metriques QoS.

Un "run" = l'execution de tous les tests du registre + un resume chiffre :
  - passed / failed / errors
  - error_rate       : part des tests non-PASS
  - availability     : part des appels HTTP ayant abouti (2xx-4xx maitrise)
  - latency_ms_avg   : latence moyenne des appels
  - latency_ms_p95   : 95e percentile (la "mauvaise experience" typique)

Le runner ne leve jamais d'exception : un test qui plante est enregistre en
ERROR, ce qui permet de conserver l'historique meme quand l'API est HS.
"""

from __future__ import annotations

import datetime as dt
import time

from .client import ApiClient
from .tests import BASE_URL, LATENCY_SLA_MS, TestResult, all_tests

API_NAME = "Frankfurter (taux de change BCE)"

# Garde-fou anti-spam : on n'envoie jamais plus de N requetes dans un run.
MAX_REQUESTS_PER_RUN = 20


def percentile(values: list[float], pct: float) -> float:
    """Percentile par interpolation lineaire (methode 'nearest-rank' adoucie)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    position = (len(ordered) - 1) * pct
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return round(ordered[low] * (1 - weight) + ordered[high] * weight, 1)


def run_tests(timeout: float = 5.0, retries: int = 1) -> dict:
    """Execute la suite complete et retourne un run serialisable en JSON."""
    client = ApiClient(BASE_URL, timeout=timeout, retries=retries)
    results: list[TestResult] = []
    latencies: list[float] = []
    started_at = time.perf_counter()

    for case in all_tests():
        if client.calls >= MAX_REQUESTS_PER_RUN:
            results.append(
                TestResult(
                    name=case.name,
                    category=case.category,
                    description=case.description,
                    status="SKIPPED",
                    details="Budget de {} requetes/run atteint (anti-spam)".format(
                        MAX_REQUESTS_PER_RUN
                    ),
                )
            )
            continue

        calls_before = client.calls
        test_started = time.perf_counter()
        status = "PASS"
        details = ""

        try:
            case.func(client)
        except AssertionError as exc:
            status = "FAIL"
            details = str(exc)
        except Exception as exc:  # bug de test, JSON casse, etc.
            status = "ERROR"
            details = "{}: {}".format(type(exc).__name__, exc)

        elapsed_ms = round((time.perf_counter() - test_started) * 1000, 1)
        calls_used = client.calls - calls_before
        if calls_used > 0:
            # Latence moyenne par appel HTTP de ce test (pauses incluses au plus juste).
            latencies.append(round(elapsed_ms / calls_used, 1))

        results.append(
            TestResult(
                name=case.name,
                category=case.category,
                description=case.description,
                status=status,
                latency_ms=elapsed_ms,
                details=details,
            )
        )

    duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
    return _build_run(results, latencies, client.calls, duration_ms, timeout)


def _build_run(
    results: list[TestResult],
    latencies: list[float],
    total_calls: int,
    duration_ms: float,
    timeout: float,
) -> dict:
    executed = [r for r in results if r.status != "SKIPPED"]
    passed = sum(1 for r in executed if r.status == "PASS")
    failed = sum(1 for r in executed if r.status == "FAIL")
    errors = sum(1 for r in executed if r.status == "ERROR")
    total = len(executed) or 1

    error_rate = round((failed + errors) / total, 4)
    availability = round(passed / total, 4)

    summary = {
        "total": len(executed),
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "error_rate": error_rate,
        "availability": availability,
        "latency_ms_avg": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        "latency_ms_p95": percentile(latencies, 0.95),
        "latency_ms_max": round(max(latencies), 1) if latencies else 0.0,
        "http_calls": total_calls,
        "duration_ms": duration_ms,
        "timeout_s": timeout,
        "latency_sla_ms": LATENCY_SLA_MS,
    }
    summary["verdict"] = verdict_for(summary)

    return {
        "api": API_NAME,
        "base_url": BASE_URL,
        "timestamp": dt.datetime.now(dt.timezone.utc)
        .astimezone()
        .replace(microsecond=0)
        .isoformat(),
        "summary": summary,
        "tests": [r.to_dict() for r in results],
    }


def verdict_for(summary: dict) -> str:
    """Traduit les chiffres en une appreciation lisible par un humain."""
    if summary["error_rate"] == 0 and summary["latency_ms_p95"] <= LATENCY_SLA_MS:
        return "OK"
    if summary["error_rate"] <= 0.2:
        return "DEGRADED"
    return "DOWN"
