# API Choice

- **Étudiant** : Safwan BEKKOUCHE (M1 Cyber)
- **API choisie** : Frankfurter — taux de change officiels de la Banque centrale européenne
- **URL base** : `https://api.frankfurter.dev` (v1)
- **Documentation officielle / README** : https://frankfurter.dev/ — dépôt : https://github.com/lineartimes/frankfurter
- **Auth** : **None** (aucune clé, aucun compte) → aucun secret à gérer côté tests
- **Pourquoi celle-ci** :
  - présente dans la whitelist PythonAnywhere (`api.frankfurter.dev`), donc joignable depuis un compte gratuit ;
  - plusieurs endpoints distincts → permet des tests de contrat variés ;
  - données **historiques figées** → un test déterministe, reproductible à l'identique ;
  - erreurs propres (404) sur entrée invalide → permet de vrais tests négatifs.

## Endpoints testés

| Méthode | Endpoint | Rôle dans la suite de tests |
|---|---|---|
| GET | `/v1/latest` | Contrat de base : statut, Content-Type, schéma, fraîcheur de la donnée |
| GET | `/v1/latest?base=EUR&symbols=USD,GBP` | Filtrage des devises + plausibilité des valeurs |
| GET | `/v1/latest?base=EUR&symbols=USD&amount=10` | Linéarité du paramètre `amount` |
| GET | `/v1/currencies` | Catalogue des devises (codes ISO-4217 → libellés) |
| GET | `/v1/2024-01-02?base=EUR&symbols=USD` | Donnée historique : test **déterministe** |
| GET | `/v1/latest?base=XXX` | Test négatif : devise inconnue → **404** attendu |
| GET | `/v1/cette-route-nexiste-pas` | Test négatif : route inconnue → **4xx** attendu |

## Hypothèses de contrat

Réponse attendue sur `/v1/latest` :

```json
{ "amount": 1.0, "base": "EUR", "date": "2026-09-21", "rates": { "USD": 1.149, "GBP": 0.8578 } }
```

| Champ | Type attendu | Contrainte vérifiée |
|---|---|---|
| `amount` | number | présent, numérique |
| `base` | string | code ISO-4217, vaut `EUR` par défaut |
| `date` | string | format `YYYY-MM-DD`, jamais dans le futur, âge ≤ 5 jours (la BCE publie en jours ouvrés) |
| `rates` | object | non vide ; chaque clé = 3 lettres majuscules ; chaque valeur = nombre > 0 |

Codes HTTP attendus :

| Situation | Code |
|---|---|
| Requête valide | `200` + `Content-Type: application/json` |
| Devise inexistante (`base=XXX`) | `404` (et surtout **pas** `500`) |
| Route inconnue | `4xx` |

Seuil de QoS retenu : **latence ≤ 2000 ms** par appel (`LATENCY_SLA_MS` dans `tester/tests.py`).

## Limites / rate limiting connu

- Aucun quota documenté ni clé requise, mais l'API est **gratuite et bénévole** : elle demande explicitement un usage raisonnable.
- Mesures prises côté solution :
  - **20 requêtes maximum par run** (`MAX_REQUESTS_PER_RUN`), la suite en consomme 15 ;
  - pause de 150 ms entre deux appels ;
  - **1 retry maximum** par appel, avec respect de l'en-tête `Retry-After` sur un `429` ;
  - cooldown de **60 s** entre deux runs déclenchés depuis le dashboard ;
  - exécution planifiée à basse fréquence (1 fois par jour sur un compte PythonAnywhere gratuit).

## Risques identifiés

| Risque | Impact | Mitigation dans la solution |
|---|---|---|
| Indisponibilité de l'API (projet communautaire) | run en échec | timeout strict 5 s + 1 retry ; le run est quand même enregistré avec le verdict `DOWN` |
| Données publiées uniquement les jours ouvrés | faux positif le week-end | tolérance de 5 jours sur l'âge de la donnée |
| Le taux de change varie entre deux appels | test `amount` instable | tolérance de 1 % sur la comparaison |
| Compte PythonAnywhere gratuit : accès Internet via proxy | aucun appel sortant possible si mal géré | `urllib` honore automatiquement `http_proxy`/`https_proxy` fournis par PythonAnywhere ; domaine présent dans la whitelist |
| `429` sous rafale | run rouge à tort | backoff + `Retry-After`, test dédié `burst_is_handled` |
| Migration du domaine (`api.frankfurter.app` → `api.frankfurter.dev`) | `301` non suivi | base URL fixée sur le domaine `.dev` actuel |

## Tests non destructifs

Tous les appels sont des `GET` en lecture seule. Aucune écriture, aucune création ni suppression de donnée côté fournisseur.
