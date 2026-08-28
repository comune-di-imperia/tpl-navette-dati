"""Sintesi giornaliera su Telegram.

Ogni mattina alle nove, ai destinatari Telegram della casella TPL: le poche
cifre del giorno prima, con il confronto sul giorno precedente. Sono le stesse
persone che ricevono gli avvisi della casella, cioe' chi segue la
sperimentazione giorno per giorno; l'elenco si governa dalla pagina Casella.

    cli sintesi-telegram              # il giorno prima
    cli sintesi-telegram --giorno 2026-08-20
    cli sintesi-telegram --prova      # scrive il messaggio senza spedirlo
"""

from __future__ import annotations

import html
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import statistiche

logger = logging.getLogger("tpl.sintesi")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

GIORNI = ("lunedi'", "martedi'", "mercoledi'", "giovedi'", "venerdi'",
          "sabato", "domenica")
MESI = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre")


def percorso_destinatari() -> Path:
    """Lo stesso elenco della sorveglianza casella, non una copia.

    Due elenchi da tenere allineati a mano sarebbero due elenchi diversi entro
    un mese: chi viene tolto dagli avvisi della casella smette di ricevere
    anche la sintesi, che e' quello che ci si aspetta.
    """
    return Path(os.environ.get("TPL_INBOX_DESTINATARI",
                               "/var/lib/tpl-inbox-watch/destinatari.json"))


def destinatari() -> List[Dict[str, str]]:
    percorso = percorso_destinatari()
    if not percorso.exists():
        return []
    try:
        dati = json.loads(percorso.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Elenco destinatari illeggibile",
                       extra={"context": {"file": str(percorso)}})
        return []
    return [v for v in dati.get("telegram", []) if v.get("chat_id")]


def in_lettere(giorno: date) -> str:
    return (f"{GIORNI[giorno.weekday()]} {giorno.day} "
            f"{MESI[giorno.month - 1]}")


def _variazione(voce: Dict[str, Any]) -> str:
    """Vuota quando non e' cambiato nulla: in un messaggio di poche righe un
    segno che dice "niente da segnalare" e' rumore."""
    if voce["verso"] == "pari":
        return ""
    freccia = "↗" if voce["verso"] == "su" else "↘"
    if voce["percentuale"] is None:
        return f"{freccia} {voce['differenza']:+g}"
    return f"{freccia} {voce['differenza']:+g} ({voce['percentuale']:+g}%)"


def componi(dati: Dict[str, Any], giorno: date) -> str:
    """Messaggio in HTML, il formato che accetta Telegram."""
    corse = dati["corse"]["totale"]
    scostamenti = {v["chiave"]: v for v in dati.get("confronto", {}).get("voci", [])}

    righe = [f"\U0001f68d <b>Navette Imperia</b> · {in_lettere(giorno)}", ""]

    def voce(etichetta: str, valore: Any, chiave: str) -> None:
        confronto = scostamenti.get(chiave)
        scarto = _variazione(confronto) if confronto else ""
        coda = f"  <i>{scarto}</i>" if scarto else ""
        righe.append(f"{etichetta}: <b>{valore}</b>{coda}")

    voce("Corse", corse, "corse")
    voce("Nuove registrazioni", dati["registrazioni"]["totale"], "registrazioni")
    voce("Valutazioni", dati["voti"]["totale"], "valutazioni")
    if dati["voti"]["media"]:
        voce("Voto medio", f"{dati['voti']['media']} / 5", "voto")
    # La durata si ricava solo dalle corse chiuse: se quasi tutte sono rimaste
    # aperte il valore mediano descrive due o tre corse, non la giornata.
    aperte = dati["corse"].get("ancora_aperte", 0)
    chiuse = corse - aperte
    if chiuse >= 3 and chiuse * 2 >= corse:
        righe.append(f"Durata tipica: <b>{dati['corse']['durata_tipica_min']} min</b>")

    salite = [f for f in dati.get("fermate", []) if f.get("quante")]
    if salite:
        righe.append("")
        righe.append("Salite per fermata:")
        for fermata in sorted(salite, key=lambda f: -f["quante"]):
            righe.append(f"  • {html.escape(fermata['nome'])}: "
                         f"{fermata['quante']}")

    if aperte:
        righe.append("")
        righe.append(f"<i>{aperte} corse risultano ancora aperte.</i>")

    return "\n".join(righe)


def _spedisci(token: str, chat_id: str, testo: str) -> bool:
    corpo = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": testo,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    try:
        richiesta = urllib.request.Request(
            TELEGRAM_API.format(token=token), data=corpo)
        with urllib.request.urlopen(richiesta, timeout=20) as risposta:
            esito = json.loads(risposta.read().decode())
    except (urllib.error.URLError, ValueError, OSError):
        logger.exception("Invio Telegram fallito",
                         extra={"context": {"chat_id": chat_id}})
        return False
    if not esito.get("ok"):
        logger.error("Telegram ha rifiutato il messaggio",
                     extra={"context": {"chat_id": chat_id,
                                        "descrizione": esito.get("description")}})
        return False
    return True


def esegui(giorno: Optional[date] = None, prova: bool = False) -> int:
    """Manda la sintesi. Restituisce a quanti e' arrivata."""
    giorno = giorno or (date.today() - timedelta(days=1))
    dati = statistiche.raccogli(periodo=(giorno, giorno))
    testo = componi(dati, giorno)

    if prova:
        print(testo)
        return 0

    # Giornata senza alcun movimento: non se ne manda notizia. Un messaggio
    # identico e vuoto ogni domenica insegna a non leggere piu' gli altri.
    if not (dati["corse"]["totale"] or dati["registrazioni"]["totale"]
            or dati["voti"]["totale"]):
        logger.info("Giornata senza attivita': sintesi non inviata",
                    extra={"context": {"giorno": giorno.isoformat()}})
        return 0

    token = os.environ.get("TPL_TELEGRAM_TOKEN", "").strip()
    if not token:
        logger.warning("TPL_TELEGRAM_TOKEN non impostato: sintesi non inviata")
        return 0

    elenco = destinatari()
    if not elenco:
        logger.info("Nessun destinatario Telegram: sintesi non inviata")
        return 0

    arrivate = sum(1 for v in elenco
                   if _spedisci(token, str(v["chat_id"]), testo))
    logger.info("Sintesi giornaliera inviata",
                extra={"context": {"giorno": giorno.isoformat(),
                                   "riuscite": arrivate,
                                   "destinatari": len(elenco)}})
    return arrivate
