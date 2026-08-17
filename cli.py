"""Gestione da riga di comando dell'applicazione TPL navette.

    python3 -m scripts.tpl_navette.cli utente-crea --utente mrossi --ruolo operatore
    python3 -m scripts.tpl_navette.cli utente-password --utente mrossi
    python3 -m scripts.tpl_navette.cli utenti
    python3 -m scripts.tpl_navette.cli registro --limite 50
    python3 -m scripts.tpl_navette.cli elabora --file archivio.zip

La password non si passa come argomento: finirebbe nella cronologia della shell
e nella lista dei processi. Viene chiesta a video o letta da stdin con --stdin.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from . import db


def _password(da_stdin: bool) -> str:
    if da_stdin:
        return sys.stdin.readline().rstrip("\n")
    prima = getpass.getpass("Password: ")
    if prima != getpass.getpass("Ripeti: "):
        sys.exit("le due password non coincidono")
    if len(prima) < 10:
        sys.exit("password troppo corta: almeno 10 caratteri")
    return prima


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tpl-navette", description=__doc__)
    sub = p.add_subparsers(dest="comando", required=True)

    c = sub.add_parser("utente-crea", help="crea un operatore")
    c.add_argument("--utente", required=True)
    c.add_argument("--nome", default="")
    c.add_argument("--email", default="")
    c.add_argument(
        "--ruolo", default="operatore", choices=["operatore", "amministratore"]
    )
    c.add_argument("--stdin", action="store_true", help="password da stdin")

    c = sub.add_parser("utente-password", help="cambia la password di un operatore")
    c.add_argument("--utente", required=True)
    c.add_argument("--stdin", action="store_true")

    sub.add_parser("utenti", help="elenca gli operatori")

    c = sub.add_parser("registro", help="mostra il registro attivita'")
    c.add_argument("--limite", type=int, default=50)
    c.add_argument("--utente", default="")

    c = sub.add_parser("elabora", help="elabora un archivio senza passare dal web")
    c.add_argument("--file", required=True)
    c.add_argument("--senza-email", action="store_true")

    a = p.parse_args(argv)
    db.inizializza()

    if a.comando == "utente-crea":
        db.crea_utente(a.utente, _password(a.stdin), a.nome, a.email, a.ruolo)
        print(f"creato {a.utente} ({a.ruolo})")

    elif a.comando == "utente-password":
        if not db.cambia_password(a.utente, _password(a.stdin)):
            sys.exit(f"utente {a.utente} inesistente")
        print(f"password aggiornata per {a.utente}")

    elif a.comando == "utenti":
        for u in db.elenco_utenti():
            stato = "attivo" if u["attivo"] else "disattivato"
            print(
                f"{u['utente']:<20} {u['ruolo']:<15} {stato:<12} "
                f"ultimo accesso: {u['ultimo_accesso'] or 'mai'}"
            )

    elif a.comando == "registro":
        for v in db.leggi_registro(a.limite, a.utente):
            print(
                f"{v['quando'][:19]}  {v['utente']:<14} {v['azione']:<14} "
                f"{v['esito']:<10} {v['dettaglio']}"
            )

    elif a.comando == "elabora":
        from . import pipeline

        esito = pipeline.elabora(Path(a.file), invia=not a.senza_email)
        print(json.dumps(esito, indent=2, ensure_ascii=False, default=str))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
