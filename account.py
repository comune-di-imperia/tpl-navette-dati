"""Ricerca e cancellazione dell'account di un cittadino.

L'applicazione non tocca il database dell'applicazione di bordo: deposita una
richiesta in una cartella condivisa e aspetta la risposta di un servizio che i
privilegi ce li ha. E' la stessa strada gia' usata per i dispositivi, con una
coda propria — cancellare i dati di una persona e abilitare un telefono non
sono operazioni dello stesso peso.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("tpl.account")

CODA = Path(os.environ.get("TPL_UTENTI_CODA", "/var/lib/tpl-utenti"))
RICHIESTE = CODA / "richieste"
RISPOSTE = CODA / "risposte"

ATTESA_S = 45.0
PAUSA_S = 0.4

PAROLA_CONFERMA = "CANCELLA"


class ErroreAccount(RuntimeError):
    """Messaggio destinato a chi sta usando la pagina, non al giornale."""


def disponibile() -> bool:
    return RICHIESTE.is_dir() and os.access(RICHIESTE, os.W_OK)


def _chiedi(richiesta: Dict[str, Any]) -> Dict[str, Any]:
    if not disponibile():
        raise ErroreAccount(
            "Il servizio di gestione degli account non e' attivo su questo "
            "server: la funzione e' disponibile solo dove gira l'applicazione "
            "dei passeggeri."
        )

    nome = f"{time.time_ns()}-{uuid.uuid4().hex[:8]}.json"
    provvisorio = RICHIESTE / (nome + ".parziale")
    definitivo = RICHIESTE / nome
    try:
        provvisorio.write_text(json.dumps(richiesta), encoding="utf-8")
        # il servizio deve trovare un file gia' completo, mai a meta'
        provvisorio.replace(definitivo)
    except OSError as errore:
        raise ErroreAccount(f"Richiesta non depositata: {errore}") from errore

    attesa_fino = time.monotonic() + ATTESA_S
    risposta_file = RISPOSTE / nome
    while time.monotonic() < attesa_fino:
        if risposta_file.exists():
            try:
                risposta = json.loads(risposta_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                time.sleep(PAUSA_S)
                continue
            finally:
                risposta_file.unlink(missing_ok=True)

            if risposta.get("esito") != "ok":
                raise ErroreAccount(
                    risposta.get("messaggio") or "Operazione rifiutata.")
            try:
                return json.loads(risposta.get("uscita") or "{}")
            except json.JSONDecodeError as errore:
                raise ErroreAccount(
                    "Risposta del servizio incomprensibile.") from errore
        time.sleep(PAUSA_S)

    definitivo.unlink(missing_ok=True)
    raise ErroreAccount(
        "Il servizio di gestione degli account non ha risposto. "
        "Verificare che sia in esecuzione."
    )


def cerca(email: str) -> Dict[str, Any]:
    """Dice se esiste un account con quell'indirizzo, e che cosa comporta.

    Si interroga sempre prima di cancellare: senza, chi usa la pagina
    premerebbe un pulsante senza sapere quante corse e quante valutazioni sta
    per rendere anonime.
    """
    return _chiedi({"azione": "cerca", "email": (email or "").strip()})


def cancella(email: str, prova: bool = False) -> Dict[str, Any]:
    """Cancella l'account. Con ``prova`` simula e annulla, senza scrivere."""
    return _chiedi({
        "azione": "cancella",
        "email": (email or "").strip(),
        "conferma": PAROLA_CONFERMA,
        "prova": bool(prova),
    })
