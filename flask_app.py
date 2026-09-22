"""Application Flask deployee sur PythonAnywhere.

Routes exposees :
  GET /                  -> dashboard (dernier run + historique + tendance)
  GET /runs/<id>         -> dashboard positionne sur un run precis
  GET /run               -> declenche un run de tests (anti-spam : 1 run / 60 s)
  GET /health            -> etat de sante de la solution (JSON, pour du monitoring)
  GET /api/runs          -> historique des runs (JSON)
  GET /api/runs/<id>     -> un run complet (JSON)
  GET /export/last.json  -> telechargement du dernier run
  GET /export/runs.json  -> telechargement de tout l'historique
  GET /consignes         -> l'enonce original de l'atelier
"""

from __future__ import annotations

import datetime as dt
import json

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

import storage
from tester.runner import API_NAME, MAX_REQUESTS_PER_RUN, run_tests
from tester.tests import BASE_URL, LATENCY_SLA_MS

app = Flask(__name__)

# Anti-spam : on refuse de relancer un run trop rapproche du precedent, pour ne
# pas marteler l'API publique depuis le bouton du dashboard.
MIN_SECONDS_BETWEEN_RUNS = 60

storage.init_db()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _seconds_since_last_run() -> float | None:
    """Secondes ecoulees depuis le dernier run, ou None s'il n'y en a aucun."""
    runs = storage.list_runs(limit=1)
    if not runs:
        return None
    try:
        previous = dt.datetime.fromisoformat(runs[0]["timestamp"])
    except (TypeError, ValueError):
        return None
    now = dt.datetime.now(previous.tzinfo) if previous.tzinfo else dt.datetime.now()
    return (now - previous).total_seconds()


def _wants_json() -> bool:
    return request.args.get("format") == "json"


def _download(payload: object, filename: str) -> Response:
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    return Response(
        body,
        mimetype="application/json",
        headers={"Content-Disposition": 'attachment; filename="{}"'.format(filename)},
    )


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@app.get("/")
@app.get("/dashboard")
def dashboard():
    return _render_dashboard(storage.last_run())


@app.get("/runs/<int:run_id>")
def run_detail(run_id: int):
    run = storage.get_run(run_id)
    if run is None:
        return _render_dashboard(None, message="Run #{} introuvable.".format(run_id)), 404
    return _render_dashboard(run)


def _render_dashboard(run: dict | None, message: str | None = None):
    return render_template(
        "dashboard.html",
        run=run,
        history=storage.list_runs(limit=25),
        trend=storage.trend(limit=25),
        stats=storage.global_stats(),
        api_name=API_NAME,
        base_url=BASE_URL,
        latency_sla_ms=LATENCY_SLA_MS,
        max_requests=MAX_REQUESTS_PER_RUN,
        cooldown=MIN_SECONDS_BETWEEN_RUNS,
        message=message or request.args.get("message"),
    )


# --------------------------------------------------------------------------- #
# Execution des tests
# --------------------------------------------------------------------------- #
@app.get("/run")
def trigger_run():
    elapsed = _seconds_since_last_run()
    if elapsed is not None and elapsed < MIN_SECONDS_BETWEEN_RUNS:
        wait = int(MIN_SECONDS_BETWEEN_RUNS - elapsed)
        note = "Anti-spam : patientez encore {} s avant de relancer un run.".format(wait)
        if _wants_json():
            return jsonify({"status": "throttled", "retry_after_s": wait, "message": note}), 429
        return redirect(url_for("dashboard", message=note))

    run = run_tests()
    run_id = storage.save_run(run, triggered_by="web")

    if _wants_json():
        run["id"] = run_id
        return jsonify(run)
    return redirect(url_for("run_detail", run_id=run_id))


# --------------------------------------------------------------------------- #
# Monitoring / API JSON
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    """Etat de sante de la solution, base sur le dernier run enregistre.

    200 -> tout va bien   |   503 -> derniere mesure degradee ou absente
    """
    latest = storage.list_runs(limit=1)
    if not latest:
        return (
            jsonify(
                {
                    "status": "unknown",
                    "api": API_NAME,
                    "detail": "Aucun run enregistre pour l'instant.",
                }
            ),
            503,
        )

    run = latest[0]
    age = _seconds_since_last_run()
    stale = age is not None and age > 3600  # plus d'une heure sans mesure
    healthy = run["verdict"] == "OK" and not stale

    payload = {
        "status": "healthy" if healthy else ("stale" if stale else "degraded"),
        "api": API_NAME,
        "base_url": BASE_URL,
        "last_run": {
            "id": run["id"],
            "timestamp": run["timestamp"],
            "verdict": run["verdict"],
            "passed": run["passed"],
            "failed": run["failed"],
            "errors": run["errors"],
            "error_rate": run["error_rate"],
            "availability": run["availability"],
            "latency_ms_avg": run["latency_ms_avg"],
            "latency_ms_p95": run["latency_ms_p95"],
        },
        "age_seconds": round(age) if age is not None else None,
        "runs_stored": storage.global_stats()["runs_count"],
    }
    return jsonify(payload), (200 if healthy else 503)


@app.get("/api/runs")
def api_runs():
    limit = min(int(request.args.get("limit", 30)), 200)
    return jsonify({"count": limit, "runs": storage.list_runs(limit=limit)})


@app.get("/api/runs/<int:run_id>")
def api_run(run_id: int):
    run = storage.get_run(run_id)
    if run is None:
        return jsonify({"error": "run introuvable", "id": run_id}), 404
    return jsonify(run)


@app.get("/export/last.json")
def export_last():
    run = storage.last_run()
    if run is None:
        return jsonify({"error": "aucun run enregistre"}), 404
    return _download(run, "last_run.json")


@app.get("/export/runs.json")
def export_runs():
    runs = storage.list_runs(limit=storage.MAX_RUNS_KEPT)
    return _download({"api": API_NAME, "count": len(runs), "runs": runs}, "runs_history.json")


# --------------------------------------------------------------------------- #
# Enonce de l'atelier (page d'origine du depot)
# --------------------------------------------------------------------------- #
@app.get("/consignes")
def consignes():
    return render_template("consignes.html")


if __name__ == "__main__":
    # Utile en local uniquement : PythonAnywhere utilise son propre serveur WSGI.
    app.run(host="0.0.0.0", port=5000, debug=True)
