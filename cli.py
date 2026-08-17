"""Gestione da riga di comando dell'applicazione TPL navette.

    python3 -m scripts.tpl_navette.cli utente-crea --utente mrossi --email m@x.it --invito
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

from . import db, permessi


def _password(da_stdin: bool) -> str:
    if da_stdin:
        return sys.stdin.readline().rstrip("\n")
    prima = getpass.getpass("Password: ")
    if prima != getpass.getpass("Ripeti: "):
        sys.exit("le due password non coincidono")
    return prima


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tpl-navette", description=__doc__)
    sub = p.add_subparsers(dest="comando", required=True)

    c = sub.add_parser("utente-crea", help="crea un'utenza")
    c.add_argument("--utente", required=True)
    c.add_argument("--nome", default="")
    c.add_argument("--email", required=True)
    c.add_argument("--ruolo", default="tecnico", choices=list(permessi.ETICHETTE))
    c.add_argument("--stdin", action="store_true", help="password da stdin")
    c.add_argument(
        "--invito",
        action="store_true",
        help="non chiedere la password: invia l'invito per email",
    )

    c = sub.add_parser("utente-password", help="cambia la password di un operatore")
    c.add_argument("--utente", required=True)
    c.add_argument("--stdin", action="store_true")

    sub.add_parser("utenti", help="elenca le utenze")
    sub.add_parser("token-pulisci", help="rimuove i token scaduti")

    c = sub.add_parser(
        "registro-archivia", help="copia su S3 i mesi conclusi e sfoltisce il database"
    )
    c.add_argument("--mesi-in-linea", type=int, default=None)
    c.add_argument(
        "--senza-eliminare",
        action="store_true",
        help="copia soltanto, non rimuove nulla dal database",
    )

    c = sub.add_parser("registro", help="mostra il registro attivita'")
    c.add_argument("--limite", type=int, default=50)
    c.add_argument("--utente", default="")

    c = sub.add_parser("elabora", help="elabora un archivio senza passare dal web")
    c.add_argument("--file", required=True)
    c.add_argument("--senza-email", action="store_true")

    a = p.parse_args(argv)
    db.inizializza()

    if a.comando == "utente-crea":
        password = "" if a.invito else _password(a.stdin)
        if password:
            motivo = db.valuta_politica_password(password, a.utente, a.email)
            if motivo:
                sys.exit(motivo)
        uid = db.crea_utente(a.utente, a.email, a.nome, a.ruolo, password)
        print(f"creato {a.utente} ({a.ruolo})")
        if a.invito:
            from . import posta

            token = db.crea_token(uid, "primo_accesso")
            posta.invia_link_password(a.email, token, "primo_accesso", a.nome, a.utente)
            print(f"invito di attivazione inviato a {a.email}")

    elif a.comando == "utente-password":
        riga = db.leggi_utente(a.utente)
        if not riga:
            sys.exit(f"utente {a.utente} inesistente")
        password = _password(a.stdin)
        motivo = db.valuta_politica_password(password, riga["utente"], riga["email"])
        if motivo:
            sys.exit(motivo)
        db.imposta_password(riga["id"], password)
        print(f"password aggiornata per {a.utente}; sessioni aperte chiuse")

    elif a.comando == "utenti":
        for u in db.elenco_utenti(includi_archiviati=True):
            attivazione = "" if u["hash_password"] else "  (da attivare)"
            print(
                f"{u['utente']:<20} {u['ruolo']:<16} {u['stato']:<12} "
                f"ultimo accesso: {u['ultimo_accesso'] or 'mai'}{attivazione}"
            )

    elif a.comando == "token-pulisci":
        print(f"token rimossi: {db.pulisci_token()}")

    elif a.comando == "registro-archivia":
        from . import archivio_registro

        esito = archivio_registro.archivia(
            mesi_in_linea=(
                a.mesi_in_linea
                if a.mesi_in_linea is not None
                else archivio_registro.MESI_IN_LINEA
            ),
            elimina=not a.senza_eliminare,
        )
        print(f"mesi esaminati:  {', '.join(esito['esaminati']) or 'nessuno'}")
        print(f"copiati su S3:   {', '.join(esito['caricati']) or 'nessuno'}")
        print(f"gia' archiviati: {', '.join(esito['gia_presenti']) or 'nessuno'}")
        for mese, quante in esito["eliminati"].items():
            print(f"eliminate dal database: {mese} ({quante} voci)")
        for mese in esito["saltati"]:
            print(f"NON eliminato (copia assente sul bucket): {mese}")

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
