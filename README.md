# 🎯 Testing as Code & API Monitoring — Frankfurter

Solution d'**automatisation des tests** d'une API publique, déployée en continu sur PythonAnywhere
via GitHub Actions. Rendu de l'atelier [ATELIER_AUTOMATISATION_TESTS](https://github.com/bstocker/ATELIER_AUTOMATISATION_TESTS) (M1).

**API surveillée** : [Frankfurter](https://frankfurter.dev/) — taux de change officiels de la BCE, sans authentification.
Le détail du contrat testé est dans [`API_CHOICE.md`](API_CHOICE.md).



| | |
|---|---|
| 🌐 **Dashboard en production** | https://VOTRE-USER.pythonanywhere.com/ |
| ❤️ **Endpoint de santé** | https://VOTRE-USER.pythonanywhere.com/health |
| 📦 **Dépôt** | ce repository |

> Remplacez `VOTRE-USER` par votre nom d'utilisateur PythonAnywhere une fois le site déployé.

---

## 📊 Ce que fait la solution

1. Elle exécute **12 tests automatisés** contre l'API Frankfurter (contrat, robustesse, QoS).
2. Elle mesure la **qualité de service** : latence moyenne, latence p95, taux d'erreur, disponibilité.
3. Elle **archive chaque run** dans une base SQLite (résumé + détail de chaque test).
4. Elle **publie un dashboard** lisible, avec le dernier run, l'historique et une courbe de tendance.
5. Elle s'exécute **automatiquement** (Scheduled Task PythonAnywhere) et se **redéploie** à chaque `push` (GitHub Actions).

---

## 🧱 Architecture

```
Repo GitHub
  └── Code Python + tests (Testing as Code)
           ↓  GitHub Action : deploy-pythonanywhere.yml (à chaque push sur main)
PythonAnywhere (Flask)
  ├── /          dashboard : dernier run, tendance, historique
  ├── /run       déclenche un run de tests (cooldown 60 s)
  ├── /health    état de santé au format JSON (200 / 503)
  └── data/runs.db   historique SQLite
           ↑
   Scheduled Task : python3.13 run_tests.py --source scheduled
```

### Arborescence

```
.
├─ flask_app.py                  # application Flask (routes web + API JSON)
├─ run_tests.py                  # point d'entrée CLI (utilisé par la tâche planifiée)
├─ storage.py                    # persistance SQLite : save_run, list_runs, trend, stats
├─ tester/
│  ├─ client.py                  # wrapper HTTP : timeout, retry, backoff 429, latence
│  ├─ tests.py                   # les 12 tests "as code"
│  └─ runner.py                  # orchestration + calcul des métriques QoS
├─ templates/
│  ├─ dashboard.html             # le dashboard
│  └─ consignes.html             # l'énoncé original de l'atelier (route /consignes)
├─ data/runs.db                  # base SQLite (générée, non versionnée)
├─ API_CHOICE.md                 # fiche de choix d'API + contrat testé
├─ requirements.txt
└─ .github/workflows/deploy-pythonanywhere.yml
```

> **Aucune dépendance externe pour les tests** : `urllib` et `sqlite3` suffisent.
> C'est volontaire — sur un compte PythonAnywhere gratuit, cela évite tout `pip install`,
> et `urllib` honore automatiquement le proxy sortant (`http_proxy`) imposé aux comptes gratuits.

---

## 🧪 Le plan de tests (12 tests)

### A. Contrat fonctionnel — 8 tests

| Test | Ce qu'il vérifie |
|---|---|
| `latest_returns_200` | `GET /v1/latest` répond bien `200` |
| `latest_content_type_is_json` | l'en-tête `Content-Type` annonce bien du JSON |
| `latest_schema_and_types` | présence de `amount`/`base`/`date`/`rates` + types + codes ISO-4217 + taux > 0 |
| `latest_date_is_valid` | date au format `YYYY-MM-DD`, non future, et fraîche (≤ 5 jours) |
| `latest_symbols_filter` | le paramètre `symbols` filtre réellement + plausibilité du taux EUR/USD |
| `currencies_catalog` | `/v1/currencies` renvoie ≥ 20 devises avec libellés non vides |
| `historical_rate_is_deterministic` | une date passée figée renvoie **toujours** la même valeur |
| `amount_is_linear` | `amount=10` donne bien 10× le taux unitaire (tolérance 1 %) |

### B. Robustesse — 3 tests

| Test | Ce qu'il vérifie |
|---|---|
| `invalid_currency_returns_404` | une devise inconnue renvoie `404` — et surtout **pas** `500` |
| `unknown_route_returns_4xx` | une route inexistante renvoie une erreur `4xx` maîtrisée |
| `burst_is_handled` | 3 appels rapprochés : aucun `5xx`, un éventuel `429` est absorbé par le backoff |

### C. Qualité de service — 1 test

| Test | Ce qu'il vérifie |
|---|---|
| `latency_under_sla` | la latence de `/v1/latest` reste sous le seuil de **2000 ms** |

### Mécanismes de robustesse implémentés (`tester/client.py`)

- **Timeout strict** de 5 s sur chaque appel.
- **1 retry maximum**, uniquement sur les erreurs *transitoires* (`429`, `5xx`, panne réseau).
- **Backoff** respectant l'en-tête `Retry-After` renvoyé par l'API (plafonné à 5 s).
- Un `4xx` n'est **pas** rejoué : c'est une réponse valide, pas une panne.
- Le client ne lève jamais d'exception : une panne réseau devient un `Response(status=None)`,
  donc un run reste exploitable même quand l'API est totalement indisponible.
- **Garde-fou anti-spam** : 20 requêtes maximum par run, pause de 150 ms entre deux appels,
  cooldown de 60 s entre deux runs déclenchés depuis le dashboard.

---

## 📈 Les métriques QoS et leur interprétation

| Métrique | Calcul | Comment la lire |
|---|---|---|
| **Taux d'erreur** | `(FAIL + ERROR) / tests exécutés` | > 0 % = le contrat de l'API n'est plus respecté |
| **Disponibilité** | `PASS / tests exécutés` | part des vérifications conformes sur le run |
| **Latence moyenne** | moyenne des latences par appel HTTP | ressenti « normal » d'un utilisateur |
| **Latence p95** | 95ᵉ percentile des latences | la *mauvaise* expérience : 5 % des appels sont plus lents |
| **Verdict** | dérivé des deux précédentes | `OK` / `DEGRADED` / `DOWN` |

Règle du verdict (`tester/runner.py`) :

```
OK        : 0 % d'erreur ET p95 ≤ SLA (2000 ms)
DEGRADED  : taux d'erreur ≤ 20 %  (ou p95 au-dessus du SLA)
DOWN      : taux d'erreur > 20 %
```

La moyenne seule ment : elle est écrasée par les appels rapides. **C'est le p95 qui doit être
surveillé**, car c'est lui qui décrit l'expérience dégradée réellement vécue par une partie des
utilisateurs. La courbe de tendance du dashboard superpose les deux pour repérer une dérive
avant qu'elle ne devienne une panne.

---

## 🌐 Routes exposées

| Route | Description |
|---|---|
| `GET /` · `GET /dashboard` | dashboard : dernier run, KPI, tendance, historique |
| `GET /runs/<id>` | rejoue l'affichage détaillé d'un run archivé |
| `GET /run` | déclenche un run (cooldown 60 s) ; `?format=json` pour la réponse brute |
| `GET /health` | **bonus** — état de santé JSON : `200` si sain, `503` si dégradé ou périmé |
| `GET /api/runs?limit=N` | historique des runs (JSON) |
| `GET /api/runs/<id>` | un run complet avec le détail des tests (JSON) |
| `GET /export/last.json` | **bonus** — téléchargement du dernier run |
| `GET /export/runs.json` | **bonus** — téléchargement de tout l'historique |
| `GET /consignes` | l'énoncé original de l'atelier |

Exemple de réponse `/health` :

```json
{
  "status": "healthy",
  "api": "Frankfurter (taux de change BCE)",
  "last_run": {
    "id": 42, "verdict": "OK", "passed": 12, "failed": 0, "errors": 0,
    "error_rate": 0.0, "availability": 1.0,
    "latency_ms_avg": 610.0, "latency_ms_p95": 685.4
  },
  "age_seconds": 120,
  "runs_stored": 42
}
```

---

## 🚀 Déploiement

### 1. Secrets GitHub

`Settings → Secrets and variables → Actions → New repository secret`

| Secret | Valeur |
|---|---|
| `PA_USERNAME` | votre nom d'utilisateur PythonAnywhere |
| `PA_TOKEN` | votre API token (PythonAnywhere → *Account* → *API Token*) |
| `PA_TARGET_DIR` | le *Source code* de la web app, ex. `/home/VOTRE-USER/mysite` |
| `PA_WEBAPP_DOMAIN` | ex. `VOTRE-USER.pythonanywhere.com` |
| `PA_HOST` *(optionnel)* | `eu.pythonanywhere.com` si votre compte est sur la région EU |

Le workflow [`deploy-pythonanywhere.yml`](.github/workflows/deploy-pythonanywhere.yml) téléverse
tous les fichiers à chaque `push` sur `main`, puis recharge la web app. `data/` est exclu de Git :
l'historique de production n'est donc jamais écrasé par un déploiement.

### 2. Configuration de la web app PythonAnywhere

Onglet **Web** → *Add a new web app* → **Flask** → **Python 3.13**.

Dans le fichier WSGI (`/var/www/VOTRE-USER_pythonanywhere_com_wsgi.py`), pointez vers ce projet :

```python
import sys
path = '/home/VOTRE-USER/mysite'
if path not in sys.path:
    sys.path.insert(0, path)

from flask_app import app as application   # noqa
```

### 3. Exécution planifiée (Scheduled Task)

Onglet **Tasks** → *Create a new scheduled task* :

```bash
cd /home/VOTRE-USER/mysite && python3.13 run_tests.py --source scheduled
```

Un compte gratuit autorise **une tâche quotidienne** ; un compte payant permet une fréquence horaire.

---

## 💻 Exécution en local

```bash
python run_tests.py                 # lance un run et l'enregistre en base
python run_tests.py --json          # sortie JSON complète
python run_tests.py --no-save       # essai à blanc, sans écriture
```

Le code de sortie vaut `0` si tous les tests passent, `1` sinon : utilisable tel quel dans une CI.

Pour lancer le dashboard en local :

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m flask --app flask_app run --port 5055
```

---

## 🧠 Troubleshooting

Logs disponibles depuis l'onglet **Web** de PythonAnywhere :

- `VOTRE-USER.pythonanywhere.com.error.log` — tracebacks Python (la première chose à lire)
- `VOTRE-USER.pythonanywhere.com.server.log` — démarrages et rechargements de la web app
- `VOTRE-USER.pythonanywhere.com.access.log` — requêtes HTTP entrantes

| Symptôme | Cause probable |
|---|---|
| Tous les tests en `FAIL` avec « Pas de réponse HTTP » | compte gratuit : domaine absent de la [whitelist PythonAnywhere](https://www.pythonanywhere.com/whitelist/) |
| L'Action GitHub échoue sur *Validate required secrets* | un des 4 secrets est manquant ou mal orthographié |
| Le site ne se met pas à jour | `PA_TARGET_DIR` ne correspond pas au *Source code* de la web app |
| `no such table: runs` | `data/` non inscriptible — la base est créée au démarrage par `storage.init_db()` |

---

## ✅ Checklist de l'atelier

- [x] Repo GitHub créé
- [x] Tests implémentés (**12** ≥ 6 requis)
- [x] Timeout strict + 1 retry max + gestion `429`/`5xx`
- [x] Enregistrement des runs en SQLite (résumé + détail)
- [x] Dashboard accessible avec historique et « dernier run »
- [x] Exécution planifiée (PythonAnywhere Scheduled Task)
- [x] **Bonus** : `/health`, export JSON téléchargeable, courbe de tendance latence/erreurs
