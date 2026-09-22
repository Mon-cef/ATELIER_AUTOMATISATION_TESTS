#!/usr/bin/env python3
"""Point d'entree en ligne de commande : execute un run et l'enregistre.

C'est ce script qui est appele par la Scheduled Task de PythonAnywhere :

    python3.13 /home/<user>/myapp/run_tests.py --source scheduled

Options :
    --source <nom>   etiquette de declenchement stockee en base (defaut: cli)
    --json           affiche le run complet en JSON sur la sortie standard
    --no-save        execute les tests sans rien ecrire en base (mode essai)

Code de sortie : 0 si tous les tests passent, 1 sinon (utile pour du monitoring).
"""

from __future__ import annotations

import argparse
import json
import sys

import storage
from tester.runner import run_tests


def main() -> int:
    parser = argparse.ArgumentParser(description="Run de tests API + enregistrement SQLite")
    parser.add_argument("--source", default="cli", help="origine du run (cli, scheduled, ...)")
    parser.add_argument("--json", action="store_true", help="sortie JSON complete")
    parser.add_argument("--no-save", action="store_true", help="ne pas ecrire en base")
    args = parser.parse_args()

    run = run_tests()
    summary = run["summary"]

    if not args.no_save:
        storage.init_db()
        run["id"] = storage.save_run(run, triggered_by=args.source)

    if args.json:
        print(json.dumps(run, indent=2, ensure_ascii=False))
    else:
        print("[{}] {} — verdict {}".format(run["timestamp"], run["api"], summary["verdict"]))
        print(
            "  {passed}/{total} tests OK · {failed} echec(s) · {errors} erreur(s) · "
            "taux d'erreur {rate:.1%}".format(
                passed=summary["passed"],
                total=summary["total"],
                failed=summary["failed"],
                errors=summary["errors"],
                rate=summary["error_rate"],
            )
        )
        print(
            "  latence moy. {avg:.0f} ms · p95 {p95:.0f} ms · max {max:.0f} ms · "
            "{calls} appels HTTP en {dur:.0f} ms".format(
                avg=summary["latency_ms_avg"],
                p95=summary["latency_ms_p95"],
                max=summary["latency_ms_max"],
                calls=summary["http_calls"],
                dur=summary["duration_ms"],
            )
        )
        if "id" in run:
            print("  enregistre en base sous l'id #{}".format(run["id"]))
        for test in run["tests"]:
            if test["status"] != "PASS":
                print("  [{}] {} : {}".format(test["status"], test["name"], test["details"]))

    return 0 if summary["failed"] + summary["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
