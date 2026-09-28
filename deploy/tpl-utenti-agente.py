#!/usr/bin/env python3
"""Esegue per conto dell'applicazione le operazioni sugli account dei cittadini.

Stessa ragione dell'agente dei dispositivi: l'applicazione web gira con
`NoNewPrivileges` e non puo' richiamare da sola un comando che passa
all'utenza di servizio dell'applicazione di bordo. Deposita una richiesta in
una cartella condivisa, e questo servizio la esegue.

**Coda separata da quella dei dispositivi, di proposito.** Abilitare un
telefono e cancellare i dati di una persona non sono operazioni dello stesso
peso: la prima si rifa', la seconda no. Tenerle in due code distinte, con due
elenchi di azioni ammesse e due servizi, fa si' che un domani si possa togliere
l'una senza toccare l'altra, e che un errore nell'una non apra la strada
all'altra.

Uso:
    tpl-utenti-agente              # servizio, resta in ascolto
    tpl-utenti-agente --un-giro    # elabora quanto presente ed esce

Copyright (c) 2026 Comune di Imperia. Licenza EUPL-1.2.
"""

import argparse
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

logger = logging.getLogger("tpl_utenti_agente")

COMANDO = "/usr/local/bin/tpl-cancella-utente"
CARTELLA = Path(os.environ.get("TPL_UTENTI_CODA", "/var/lib/tpl-utenti"))
RICHIESTE = CARTELLA / "richieste"
RISPOSTE = CARTELLA / "risposte"

ATTESA_COMANDO_S = 40
PAUSA_S = 1.0
SCADENZA_S = 120

AZIONI = ("cerca", "cancella")
# Volutamente permissiva sulla forma, perche' la verifica vera la fa il
# database: un indirizzo che non esiste torna "non trovato", e va bene cosi'.
# Serve solo a escludere cio' che non e' un indirizzo e non deve finire in una
# riga di comando.
RE_EMAIL = re.compile(r"^[^@\s,;'\"\\]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
PAROLA_CONFERMA = "CANCELLA"


def argomenti_validi(richiesta: dict) -> list:
    """Traduce la richiesta in argomenti, rifiutando tutto il resto."""
    azione = richiesta.get("azione")
    if azione not in AZIONI:
        raise ValueError("Operazione non prevista.")

    email = (richiesta.get("email") or "").strip()
    if not RE_EMAIL.match(email):
        raise ValueError("Indirizzo di posta non valido.")

    if azione == "cerca":
        return ["cerca", "--email", email]

    # La parola di conferma la pretende anche il comando sottostante. Chiederla
    # due volte non e' ridondanza: qui dentro passa solo cio' che e' stato
    # deciso a monte, e una richiesta scritta da chiunque altro non basta a
    # cancellare nessuno.
    if (richiesta.get("conferma") or "").strip().upper() != PAROLA_CONFERMA:
        raise ValueError("Conferma mancante.")

    argomenti = ["cancella", "--email", email, "--conferma", PAROLA_CONFERMA]
    if richiesta.get("prova"):
        argomenti.append("--prova")
    return argomenti


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

    uscita = (completato.stdout or "").strip()
    # Il comando parla JSON anche quando si ferma: "non trovato" esce con
    # codice 1 ma e' una risposta, non un guasto, e va riportata com'e'.
    if uscita.startswith("{"):
        return {"esito": "ok", "uscita": uscita}
    if completato.returncode != 0:
        return {"esito": "errore",
                "messaggio": (completato.stderr or uscita or "").strip()[:400]}
    return {"esito": "ok", "uscita": uscita}


def _scrivi_risposta(nome: str, contenuto: dict) -> None:
    RISPOSTE.mkdir(parents=True, exist_ok=True)
    definitivo = RISPOSTE / nome
    provvisorio = definitivo.with_suffix(".parziale")
    provvisorio.write_text(json.dumps(contenuto), encoding="utf-8")
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
        # L'indirizzo non finisce nel giornale: e' un dato personale, e un
        # registro di sistema non e' il posto dove conservarlo. Che cosa e'
        # stato fatto e a chi sta nel registro dell'applicazione, che e'
        # consultabile da chi ha titolo.
        logger.info(
            "Richiesta elaborata",
            extra={"context": {"azione": richiesta.get("azione"),
                               "prova": bool(richiesta.get("prova")),
                               "esito": risposta.get("esito")}},
        )
        _scrivi_risposta(percorso.name, risposta)
        percorso.unlink(missing_ok=True)
        trattate += 1
    return trattate


def pulisci_vecchie() -> None:
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
        if time.monotonic() - ultima_pulizia > 60:
            pulisci_vecchie()
            ultima_pulizia = time.monotonic()
        time.sleep(PAUSA_S)


if __name__ == "__main__":
    raise SystemExit(main())
