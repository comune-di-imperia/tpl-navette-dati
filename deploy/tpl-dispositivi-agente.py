#!/usr/bin/env python3
"""Esegue le operazioni sui dispositivi di bordo richieste dall'applicazione.

L'applicazione web gira volutamente con `NoNewPrivileges`: non puo' elevare i
privilegi, quindi non puo' richiamare da sola il comando di gestione dei
dispositivi. Toglierle quella protezione per una funzione usata qualche volta
l'anno sarebbe un cattivo affare: riceve caricamenti da rete, ed e' proprio la
superficie che quella direttiva difende.

Qui si fa il contrario: l'applicazione **deposita una richiesta** in una
cartella condivisa e questo servizio, che i privilegi ce li ha, la esegue e
scrive la risposta accanto. L'applicazione non ottiene nuovi poteri: ottiene
una risposta.

Le richieste ammesse sono solo quelle previste, con argomenti verificati: chi
riuscisse a scrivere nella cartella non potrebbe far eseguire altro.

Uso:
    tpl-dispositivi-agente.py            # servizio, resta in ascolto
    tpl-dispositivi-agente.py --un-giro  # elabora quanto presente ed esce

Copyright (c) 2026 Comune di Imperia. Licenza EUPL-1.2.
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("tpl_dispositivi_agente")

COMANDO = "/usr/local/bin/imp-dispositivi"
CARTELLA = Path(os.environ.get("TPL_DISPOSITIVI_CODA", "/var/lib/tpl-dispositivi"))
RICHIESTE = CARTELLA / "richieste"
RISPOSTE = CARTELLA / "risposte"

ATTESA_COMANDO_S = 30
PAUSA_S = 1.0
# Una richiesta piu' vecchia di cosi' non interessa piu' a nessuno: chi
# aspettava ha gia' visto un errore di attesa scaduta.
SCADENZA_S = 120

AZIONI = ("elenco", "crea", "rigenera", "revoca")
RE_ID = re.compile(r"^[0-9a-f-]{36}$")
RE_MEZZO = re.compile(r"^[A-Z0-9][A-Z0-9 _-]{0,29}$")


def argomenti_validi(richiesta: dict) -> list:
    """Traduce la richiesta in argomenti, rifiutando tutto il resto.

    Solleva ValueError con un messaggio comprensibile: finisce sotto gli occhi
    di chi sta usando la pagina, non nei log.
    """
    azione = richiesta.get("azione")
    if azione not in AZIONI:
        raise ValueError("Operazione non prevista.")

    if azione == "elenco":
        return ["elenco"]

    if azione == "crea":
        etichetta = (richiesta.get("etichetta") or "").strip()
        mezzo = (richiesta.get("mezzo") or "").strip().upper()
        if not etichetta or len(etichetta) > 60:
            raise ValueError("Etichetta mancante o troppo lunga.")
        if not RE_MEZZO.match(mezzo):
            raise ValueError("Codice del mezzo non valido.")
        return ["crea", "--etichetta", etichetta, "--mezzo", mezzo]

    identificativo = (richiesta.get("id") or "").strip()
    if not RE_ID.match(identificativo):
        raise ValueError("Identificativo del dispositivo non valido.")
    return [azione, identificativo]


def esegui(richiesta: dict) -> dict:
    try:
        argomenti = argomenti_validi(richiesta)
    except ValueError as errore:
        return {"esito": "rifiutata", "messaggio": str(errore)}

    try:
        completato = subprocess.run(
            [COMANDO, *argomenti],
            capture_output=True,
            text=True,
            timeout=ATTESA_COMANDO_S,
        )
    except subprocess.TimeoutExpired:
        return {"esito": "errore", "messaggio": "Il comando non ha risposto in tempo."}
    except OSError as errore:
        return {"esito": "errore", "messaggio": f"Comando non eseguibile: {errore}"}

    if completato.returncode != 0:
        return {
            "esito": "errore",
            "messaggio": (completato.stderr or completato.stdout or "").strip()[:400],
        }
    return {"esito": "ok", "uscita": completato.stdout}


def _scrivi_risposta(nome: str, contenuto: dict) -> None:
    RISPOSTE.mkdir(parents=True, exist_ok=True)
    definitivo = RISPOSTE / nome
    provvisorio = definitivo.with_suffix(".parziale")
    provvisorio.write_text(json.dumps(contenuto), encoding="utf-8")
    # chi legge deve trovare o niente o un file completo, mai un file a meta'
    provvisorio.replace(definitivo)
    os.chmod(definitivo, 0o640)


def un_giro() -> int:
    if not RICHIESTE.is_dir():
        return 0
    trattate = 0
    for percorso in sorted(RICHIESTE.glob("*.json")):
        try:
            if time.time() - percorso.stat().st_mtime > SCADENZA_S:
                percorso.unlink(missing_ok=True)
                continue
            richiesta = json.loads(percorso.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            percorso.unlink(missing_ok=True)
            continue

        risposta = esegui(richiesta)
        logger.info(
            "Richiesta elaborata",
            extra={
                "context": {
                    "azione": richiesta.get("azione"),
                    "esito": risposta.get("esito"),
                }
            },
        )
        _scrivi_risposta(percorso.name, risposta)
        percorso.unlink(missing_ok=True)
        trattate += 1
    return trattate


def pulisci_vecchie() -> None:
    """Le risposte che nessuno ha ritirato non devono restare in giro."""
    if not RISPOSTE.is_dir():
        return
    limite = time.time() - SCADENZA_S
    for percorso in RISPOSTE.glob("*.json"):
        try:
            if percorso.stat().st_mtime < limite:
                percorso.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--un-giro", action="store_true", help="elabora ed esci")
    argomenti = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    if argomenti.un_giro:
        un_giro()
        pulisci_vecchie()
        return 0

    logger.info("In ascolto", extra={"context": {"cartella": str(RICHIESTE)}})
    ultima_pulizia = 0.0
    while True:
        un_giro()
        if time.time() - ultima_pulizia > 60:
            pulisci_vecchie()
            ultima_pulizia = time.time()
        time.sleep(PAUSA_S)


if __name__ == "__main__":
    sys.exit(main())
