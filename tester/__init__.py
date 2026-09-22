"""Package de test automatise de l'API Frankfurter.

  client.py  -> wrapper HTTP (timeout, retry, backoff 429, mesure de latence)
  tests.py   -> les cas de test "as code"
  runner.py  -> orchestration + calcul des metriques QoS
"""

from .runner import API_NAME, run_tests

__all__ = ["run_tests", "API_NAME"]
