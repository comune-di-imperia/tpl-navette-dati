#!/usr/bin/env python3
"""Cerca e cancella l'account di un cittadino su richiesta dell'interessato.

L'applicazione di bordo espone la cancellazione solo alla persona autenticata
sul proprio account: non ha un ruolo amministrativo, quindi chi riceve la
richiesta per posta non ha alcun modo di darle corso. Questo strumento colma
quel vuoto **senza reimplementare nulla**: apre una sessione sul database
dell'applicazione e richiama la sua funzione `anonimizza_e_cancella`, che e'
la stessa usata dal pulsante dentro l'app.

Riusare la loro funzione, invece di scrivere le nostre cancellazioni, non e'
pigrizia: quella funzione congela le caratteristiche statistiche sui viaggi
prima di eliminare il profilo, e l'ordine dei due passi non e' negoziabile —
invertirlo distrugge i dati di ricerca. Se un domani il fornitore cambia il
modello, cambia anche la funzione, e noi la seguiamo.

    tpl-cancella-utente cerca --email tizio@example.org
    tpl-cancella-utente cancella --email tizio@example.org --conferma CANCELLA --prova
    tpl-cancella-utente cancella --email tizio@example.org --conferma CANCELLA

La prova esegue tutto dentro una transazione e poi la annulla: dice che cosa
sparirebbe senza far sparire niente.

Copyright (c) 2026 Comune di Imperia. Licenza EUPL-1.2.
"""

import argparse
import json
import sys

sys.path.insert(0, "/opt/imp-qr-code/app")

PAROLA_CONFERMA = "CANCELLA"


def _configura() -> None:
    """Carica la configurazione dell'applicazione di bordo.

    Si usa il caricatore del fornitore, non una copia: il suo modulo degli
    strumenti non importa nulla dell'applicazione in cima al file proprio
    perche' la configurazione va letta prima, ed e' quindi importabile senza
    effetti. La stringa di connessione contiene una password e deve restare
    dov'e', in un file a permessi 0600 leggibile dalla sola utenza di servizio.
    """
    from app.strumenti import PERCORSO_AMBIENTE, _carica_ambiente

    _carica_ambiente(PERCORSO_AMBIENTE)


def _esci(dati: dict, codice: int = 0):
    print(json.dumps(dati, ensure_ascii=False, default=str))
    raise SystemExit(codice)


def _trova(db, email: str):
    from sqlalchemy import func, select

    from app.models import Utente

    # L'email e' dichiarata `citext` nel modello, ma il confronto si fa
    # comunque senza distinzione di maiuscole: se un domani la colonna
    # tornasse `text`, questo comando continuerebbe a trovare la persona.
    return db.scalars(
        select(Utente).where(func.lower(Utente.email) == email.strip().lower())
    ).first()


def _riepilogo(db, utente) -> dict:
    from sqlalchemy import func, select

    from app.models import Valutazione, Viaggio

    def quanti(modello):
        return db.scalar(
            select(func.count()).select_from(modello)
            .where(modello.utente_id == utente.id)
        ) or 0

    return {
        "trovato": True,
        "id": str(utente.id),
        "email_verificata": bool(utente.email_verificata),
        "creato_il": utente.creato_il,
        "ultimo_accesso": utente.ultimo_accesso,
        "gia_cancellato": utente.cancellato_il is not None,
        "viaggi": quanti(Viaggio),
        "valutazioni": quanti(Valutazione),
    }


def cerca(argomenti) -> None:
    _configura()
    from app.db import SessioneLocale

    db = SessioneLocale()
    try:
        utente = _trova(db, argomenti.email)
        if utente is None:
            _esci({"trovato": False, "email": argomenti.email})
        _esci(_riepilogo(db, utente))
    finally:
        db.close()


def cancella(argomenti) -> None:
    _configura()
    from app import cancellazione
    from app.db import SessioneLocale

    if (argomenti.conferma or "").strip().upper() != PAROLA_CONFERMA:
        _esci({"esito": "rifiutato",
               "messaggio": f"serve --conferma {PAROLA_CONFERMA}"}, 2)

    db = SessioneLocale()
    try:
        utente = _trova(db, argomenti.email)
        if utente is None:
            _esci({"esito": "non trovato", "email": argomenti.email}, 1)

        prima = _riepilogo(db, utente)
        conteggi = cancellazione.anonimizza_e_cancella(db, utente)

        if argomenti.prova:
            # Nulla esce da qui: la transazione si annulla e il database resta
            # come prima. Serve a vedere i numeri prima di decidere.
            db.rollback()
            _esci({"esito": "prova", "email": argomenti.email,
                   "prima": prima, "anonimizzati": conteggi})

        db.commit()
        _esci({"esito": "cancellato", "email": argomenti.email,
               "prima": prima, "anonimizzati": conteggi})
    except Exception as errore:  # noqa: BLE001
        db.rollback()
        _esci({"esito": "errore", "messaggio": f"{type(errore).__name__}: {errore}"}, 3)
    finally:
        db.close()


def main() -> None:
    p = argparse.ArgumentParser(prog="tpl-cancella-utente", description=__doc__)
    sub = p.add_subparsers(dest="comando", required=True)

    c = sub.add_parser("cerca", help="dice se esiste un account con quell'indirizzo")
    c.add_argument("--email", required=True)
    c.set_defaults(funzione=cerca)

    d = sub.add_parser("cancella", help="cancella l'account e anonimizza i viaggi")
    d.add_argument("--email", required=True)
    d.add_argument("--conferma", default="")
    d.add_argument("--prova", action="store_true",
                   help="simula dentro una transazione e annulla")
    d.set_defaults(funzione=cancella)

    argomenti = p.parse_args()
    argomenti.funzione(argomenti)


if __name__ == "__main__":
    main()
