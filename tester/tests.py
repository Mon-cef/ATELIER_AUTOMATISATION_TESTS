"""Tests "as code" de l'API Frankfurter (taux de change BCE, sans auth).

Chaque test est une fonction decoree par @test(...) qui recoit un ApiClient
et leve une AssertionError (via check()) si le contrat n'est pas respecte.

Trois categories :
  - contract   : le contrat fonctionnel (codes HTTP, schema, types, valeurs)
  - robustness : comportement face aux entrees invalides / 429 / 5xx
  - qos        : seuils de qualite de service (latence)

Convention de statut :
  PASS  -> toutes les assertions passent
  FAIL  -> une assertion echoue (l'API ne respecte pas le contrat attendu)
  ERROR -> exception imprevue dans le test lui-meme (bug de test, parse JSON...)
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Callable

from .client import ApiClient, Response

BASE_URL = "https://api.frankfurter.dev"

# Seuils QoS (documentes, donc discutables en soutenance)
LATENCY_SLA_MS = 2000.0        # une reponse "acceptable" pour l'utilisateur final
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Date historique figee : la BCE ne reecrit pas le passe, ce test est deterministe.
HISTORICAL_DATE = "2024-01-02"


# --------------------------------------------------------------------------- #
# Micro-framework de test (registre + assertions lisibles)
# --------------------------------------------------------------------------- #
@dataclass
class TestCase:
    name: str
    category: str
    description: str
    func: Callable[[ApiClient], None]


@dataclass
class TestResult:
    name: str
    category: str
    description: str
    status: str                       # PASS | FAIL | ERROR
    latency_ms: float = 0.0
    http_status: int | None = None
    details: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "http_status": self.http_status,
            "details": self.details,
        }


REGISTRY: list[TestCase] = []


def test(category: str, description: str):
    """Decorateur d'enregistrement d'un test dans le registre global."""

    def wrapper(func: Callable[[ApiClient], None]) -> Callable[[ApiClient], None]:
        REGISTRY.append(
            TestCase(
                name=func.__name__,
                category=category,
                description=description,
                func=func,
            )
        )
        return func

    return wrapper


def check(condition: bool, message: str) -> None:
    """Assertion explicite (ne disparait pas avec python -O, contrairement a assert)."""
    if not condition:
        raise AssertionError(message)


def check_http(resp: Response, expected: int) -> None:
    """Verifie le code HTTP en produisant un message exploitable en cas d'echec."""
    if resp.status is None:
        raise AssertionError("Pas de reponse HTTP (" + str(resp.error) + ") sur " + resp.url)
    check(
        resp.status == expected,
        "HTTP {} recu, {} attendu sur {}".format(resp.status, expected, resp.url),
    )


def json_of(resp: Response):
    """Parse le JSON en transformant une erreur de parsing en echec de contrat."""
    try:
        return resp.json()
    except ValueError as exc:
        raise AssertionError(
            "Corps non-JSON ({}) : {}".format(exc, resp.body[:120])
        ) from exc


# --------------------------------------------------------------------------- #
# A. Tests de contrat
# --------------------------------------------------------------------------- #
@test("contract", "GET /v1/latest repond bien 200 OK")
def latest_returns_200(client: ApiClient) -> None:
    resp = client.get("/v1/latest")
    check_http(resp, 200)


@test("contract", "GET /v1/latest renvoie bien du JSON (Content-Type)")
def latest_content_type_is_json(client: ApiClient) -> None:
    resp = client.get("/v1/latest")
    check_http(resp, 200)
    check(
        "application/json" in resp.content_type,
        "Content-Type inattendu : " + repr(resp.content_type),
    )


@test("contract", "Champs obligatoires presents et correctement types")
def latest_schema_and_types(client: ApiClient) -> None:
    resp = client.get("/v1/latest", params={"base": "EUR"})
    check_http(resp, 200)
    payload = json_of(resp)

    check(isinstance(payload, dict), "La racine du JSON doit etre un objet")
    for field_name in ("amount", "base", "date", "rates"):
        check(field_name in payload, "Champ obligatoire manquant : " + field_name)

    check(isinstance(payload["amount"], (int, float)), "amount doit etre un nombre")
    check(isinstance(payload["base"], str), "base doit etre une chaine")
    check(isinstance(payload["date"], str), "date doit etre une chaine")
    check(isinstance(payload["rates"], dict), "rates doit etre un objet")
    check(len(payload["rates"]) > 0, "rates ne doit pas etre vide")
    check(
        payload["base"] == "EUR",
        "base attendue EUR, recue " + repr(payload["base"]),
    )

    for code, value in payload["rates"].items():
        check(
            isinstance(code, str) and len(code) == 3 and code.isupper(),
            "Code devise invalide : " + repr(code) + " (attendu ISO-4217)",
        )
        check(
            isinstance(value, (int, float)) and not isinstance(value, bool),
            "Taux non numerique pour " + code + " : " + repr(value),
        )
        check(value > 0, "Taux negatif ou nul pour " + code + " : " + str(value))


@test("contract", "Le champ 'date' est une date ISO valide et non future")
def latest_date_is_valid(client: ApiClient) -> None:
    resp = client.get("/v1/latest")
    check_http(resp, 200)
    payload = json_of(resp)

    raw = payload.get("date")
    check(
        bool(DATE_RE.match(str(raw))),
        "Format de date invalide : " + repr(raw) + " (attendu YYYY-MM-DD)",
    )

    published = dt.date.fromisoformat(raw)
    today = dt.date.today()
    check(
        published <= today,
        "Date de publication dans le futur : {} > {}".format(published, today),
    )
    # La BCE publie en jours ouvres : on tolere un week-end + un jour ferie.
    age = (today - published).days
    check(age <= 5, "Donnees perimees : publiees le {} ({} jours)".format(published, age))


@test("contract", "Le parametre 'symbols' filtre reellement les devises retournees")
def latest_symbols_filter(client: ApiClient) -> None:
    resp = client.get("/v1/latest", params={"base": "EUR", "symbols": "USD,GBP"})
    check_http(resp, 200)
    payload = json_of(resp)

    rates = payload.get("rates", {})
    check(
        set(rates) == {"USD", "GBP"},
        "Filtre ignore : devises retournees " + str(sorted(rates)),
    )
    # Garde-fou de plausibilite : un EUR/USD hors de cette plage = donnee suspecte.
    check(
        0.5 < rates["USD"] < 2.5,
        "Taux EUR/USD implausible : " + str(rates["USD"]),
    )


@test("contract", "GET /v1/currencies expose le catalogue des devises")
def currencies_catalog(client: ApiClient) -> None:
    resp = client.get("/v1/currencies")
    check_http(resp, 200)
    payload = json_of(resp)

    check(isinstance(payload, dict), "Le catalogue doit etre un objet code -> libelle")
    check(len(payload) >= 20, "Catalogue anormalement court : " + str(len(payload)))
    for expected in ("EUR", "USD", "GBP", "JPY"):
        check(expected in payload, "Devise majeure absente du catalogue : " + expected)
    for code, label in payload.items():
        check(
            isinstance(label, str) and bool(label),
            "Libelle invalide pour " + str(code) + " : " + repr(label),
        )


@test("contract", "Une date historique figee renvoie toujours la meme donnee")
def historical_rate_is_deterministic(client: ApiClient) -> None:
    resp = client.get("/v1/" + HISTORICAL_DATE, params={"base": "EUR", "symbols": "USD"})
    check_http(resp, 200)
    payload = json_of(resp)

    check(
        payload.get("date") == HISTORICAL_DATE,
        "Date retournee " + repr(payload.get("date")) + ", " + HISTORICAL_DATE + " attendue",
    )
    usd = payload.get("rates", {}).get("USD")
    check(
        isinstance(usd, (int, float)),
        "Taux USD absent ou non numerique : " + repr(usd),
    )
    # Valeur de reference constatee sur l'API : le passe ne bouge pas.
    check(
        1.0 < usd < 1.3,
        "Taux historique EUR/USD du " + HISTORICAL_DATE + " inattendu : " + str(usd),
    )


@test("contract", "Le parametre 'amount' applique un facteur d'echelle lineaire")
def amount_is_linear(client: ApiClient) -> None:
    unit = client.get("/v1/latest", params={"base": "EUR", "symbols": "USD", "amount": 1})
    check_http(unit, 200)
    scaled = client.get("/v1/latest", params={"base": "EUR", "symbols": "USD", "amount": 10})
    check_http(scaled, 200)

    rate_unit = json_of(unit)["rates"]["USD"]
    rate_scaled = json_of(scaled)["rates"]["USD"]
    expected = rate_unit * 10
    # Tolerance 1% : l'API arrondit, et le taux peut changer entre les deux appels.
    check(
        abs(rate_scaled - expected) <= expected * 0.01,
        "amount=10 devrait donner ~{:.4f}, recu {}".format(expected, rate_scaled),
    )


# --------------------------------------------------------------------------- #
# B. Tests de robustesse
# --------------------------------------------------------------------------- #
@test("robustness", "Une devise inexistante renvoie 404 et non une 500")
def invalid_currency_returns_404(client: ApiClient) -> None:
    resp = client.get("/v1/latest", params={"base": "XXX"})
    check(
        resp.status != 500,
        "L'API expose une erreur serveur sur une entree utilisateur invalide",
    )
    check(
        resp.status == 404,
        "HTTP {} recu pour une devise inconnue, 404 attendu".format(resp.status),
    )


@test("robustness", "Une route inconnue renvoie une erreur 4xx maitrisee")
def unknown_route_returns_4xx(client: ApiClient) -> None:
    resp = client.get("/v1/cette-route-nexiste-pas")
    check(resp.status is not None, "Aucune reponse HTTP : " + str(resp.error))
    check(
        400 <= resp.status < 500,
        "HTTP {} recu sur une route inconnue, 4xx attendu".format(resp.status),
    )


@test("robustness", "Trois appels rapproches : pas de 5xx, un 429 eventuel est absorbe")
def burst_is_handled(client: ApiClient) -> None:
    statuses = []
    for _ in range(3):
        resp = client.get("/v1/latest", params={"base": "EUR", "symbols": "USD"})
        statuses.append(resp.status)
        check(resp.status is not None, "Appel en echec reseau : " + str(resp.error))
        check(
            resp.status != 429,
            "429 non absorbe apres {} tentative(s) : backoff insuffisant".format(resp.attempts),
        )
        check(
            resp.status < 500,
            "Erreur serveur {} sous rafale".format(resp.status),
        )
    check(
        all(s == 200 for s in statuses),
        "Rafale degradee : codes obtenus " + str(statuses),
    )


# --------------------------------------------------------------------------- #
# C. Test de QoS
# --------------------------------------------------------------------------- #
@test("qos", "Latence de /v1/latest sous le seuil de " + str(int(LATENCY_SLA_MS)) + " ms")
def latency_under_sla(client: ApiClient) -> None:
    resp = client.get("/v1/latest", params={"base": "EUR", "symbols": "USD"})
    check_http(resp, 200)
    check(
        resp.latency_ms <= LATENCY_SLA_MS,
        "Latence {:.0f} ms > SLA {:.0f} ms".format(resp.latency_ms, LATENCY_SLA_MS),
    )


def all_tests() -> list[TestCase]:
    """Retourne les tests dans leur ordre de declaration."""
    return list(REGISTRY)
