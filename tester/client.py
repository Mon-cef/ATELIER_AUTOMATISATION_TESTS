"""Wrapper HTTP minimaliste (stdlib uniquement).

Pourquoi la stdlib (urllib) plutot que `requests` ?
  - Aucune dependance a installer sur PythonAnywhere (compte gratuit).
  - `urllib.request` honore automatiquement les variables d'environnement
    http_proxy / https_proxy, que PythonAnywhere positionne pour les comptes
    gratuits (proxy.server:3128). Le code marche donc en local ET en prod.

Responsabilites de ce module :
  - imposer un timeout strict,
  - mesurer la latence de chaque appel,
  - rejouer une fois (1 retry max) en cas d'erreur reseau / 5xx / 429,
  - respecter l'en-tete Retry-After sur un 429 (backoff),
  - ne jamais lever d'exception : un echec reseau devient une Response(ok=False).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

DEFAULT_TIMEOUT = 5.0          # secondes : timeout strict demande (3-5s)
DEFAULT_RETRIES = 1            # 1 retry maximum (consigne)
DEFAULT_BACKOFF = 1.0          # secondes d'attente avant le retry
MAX_BACKOFF = 5.0              # on n'attend jamais plus longtemps
USER_AGENT = "atelier-qos-tester/1.0 (+https://github.com/)"

# Codes que l'on considere comme "transitoires" : ils meritent un retry.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class Response:
    """Resultat normalise d'un appel HTTP (jamais d'exception qui fuit)."""

    url: str
    status: int | None                      # None = pas de reponse HTTP (timeout, DNS...)
    latency_ms: float = 0.0
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    error: str | None = None                # message technique si echec reseau
    attempts: int = 1                       # nombre de tentatives consommees

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    def json(self) -> Any:
        """Parse le corps en JSON. Leve ValueError si ce n'est pas du JSON."""
        return json.loads(self.body)


class ApiClient:
    """Client HTTP pour une API REST publique.

    Usage :
        client = ApiClient("https://api.frankfurter.dev")
        r = client.get("/v1/latest", params={"base": "EUR"})
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        backoff: float = DEFAULT_BACKOFF,
        pause_between_calls: float = 0.15,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        # Petite pause anti-spam entre deux appels : on reste un bon citoyen.
        self.pause_between_calls = pause_between_calls
        # Compteur de requetes reelles (retries inclus) : garde-fou anti-spam.
        self.calls = 0

    # ------------------------------------------------------------------ #
    # API publique
    # ------------------------------------------------------------------ #
    def get(self, path: str, params: dict[str, Any] | None = None) -> Response:
        """GET avec timeout, mesure de latence, retry et backoff 429."""
        url = self._build_url(path, params)
        last: Response | None = None

        for attempt in range(1, self.retries + 2):   # 1 essai + N retries
            if attempt > 1 or self.pause_between_calls:
                time.sleep(self.pause_between_calls if attempt == 1 else 0)

            self.calls += 1
            resp = self._single_get(url)
            resp.attempts = attempt
            last = resp

            transient = resp.status is None or resp.status in RETRYABLE_STATUS
            if not transient or attempt == self.retries + 1:
                return resp

            time.sleep(self._retry_delay(resp))

        return last  # type: ignore[return-value]

    # ------------------------------------------------------------------ #
    # Interne
    # ------------------------------------------------------------------ #
    def _build_url(self, path: str, params: dict[str, Any] | None) -> str:
        url = self.base_url + "/" + path.lstrip("/")
        if params:
            url += "?" + urllib.parse.urlencode(params, safe=",")
        return url

    def _retry_delay(self, resp: Response) -> float:
        """Backoff : on suit Retry-After si l'API nous l'indique (429)."""
        retry_after = resp.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), MAX_BACKOFF)
            except ValueError:
                pass
        return self.backoff

    def _single_get(self, url: str) -> Response:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            method="GET",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as http:
                body = http.read().decode("utf-8", errors="replace")
                return Response(
                    url=url,
                    status=http.status,
                    latency_ms=self._elapsed_ms(started),
                    headers=self._normalize_headers(http.headers.items()),
                    body=body,
                )
        except urllib.error.HTTPError as exc:
            # 4xx / 5xx : ce n'est PAS une panne, c'est une reponse HTTP valide.
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            return Response(
                url=url,
                status=exc.code,
                latency_ms=self._elapsed_ms(started),
                headers=self._normalize_headers(exc.headers.items()),
                body=body,
            )
        except urllib.error.URLError as exc:
            # DNS, refus de connexion, proxy injoignable, timeout socket...
            return Response(
                url=url,
                status=None,
                latency_ms=self._elapsed_ms(started),
                error=f"{type(exc.reason).__name__ if exc.reason else 'URLError'}: {exc.reason}",
            )
        except TimeoutError:
            return Response(
                url=url,
                status=None,
                latency_ms=self._elapsed_ms(started),
                error=f"Timeout apres {self.timeout}s",
            )
        except Exception as exc:  # filet de securite : un run ne doit jamais crasher
            return Response(
                url=url,
                status=None,
                latency_ms=self._elapsed_ms(started),
                error=f"{type(exc).__name__}: {exc}",
            )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 1)

    @staticmethod
    def _normalize_headers(items) -> dict[str, str]:
        return {str(k).lower(): str(v) for k, v in items}
