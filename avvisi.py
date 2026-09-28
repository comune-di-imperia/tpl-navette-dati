"""Avvisa chi segue la casella di cio' che un amministratore ha fatto.

Chi riceve gli avvisi di arrivo deve ricevere anche quelli di risposta: senza,
un messaggio segnalato a tre persone resta, per due di loro, in attesa per
sempre — e la seconda che apre la casella riscrive al cittadino che ha gia'
avuto risposta.

I destinatari sono gli stessi della sorveglianza casella, email e Telegram, e
non un secondo elenco: due elenchi da tenere allineati a mano sono due elenchi
diversi entro un mese.
"""

from __future__ import annotations

import html as _html
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List

logger = logging.getLogger("tpl.avvisi")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _telegram(testo: str) -> int:
    from . import casella

    token = os.environ.get("TPL_TELEGRAM_TOKEN", "").strip()
    if not token:
        logger.info("Telegram non configurato: avviso non inviato")
        return 0

    destinatari = [str(v["chat_id"])
                   for v in casella.leggi_destinatari().get("telegram", [])
                   if v.get("chat_id")]
    arrivati = 0
    for chat_id in destinatari:
        corpo = urllib.parse.urlencode({
            "chat_id": chat_id, "text": testo, "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        try:
            richiesta = urllib.request.Request(
                TELEGRAM_API.format(token=token), data=corpo)
            with urllib.request.urlopen(richiesta, timeout=20) as risposta:
                esito = json.loads(risposta.read().decode())
            if esito.get("ok"):
                arrivati += 1
            else:
                logger.error("Telegram ha rifiutato l'avviso",
                             extra={"context": {"descrizione": esito.get("description")}})
        except (urllib.error.URLError, ValueError, OSError):
            logger.exception("Invio Telegram fallito")
    return arrivati


def _email(oggetto: str, corpo: str) -> int:
    from . import casella, posta

    elenco = casella.leggi_destinatari().get("email", [])
    destinatari = [v["indirizzo"] for v in elenco if v.get("indirizzo")]
    if not destinatari or not posta.configurata():
        return 0
    try:
        posta.invia(destinatari=destinatari, oggetto=oggetto, corpo=corpo)
    except Exception:  # noqa: BLE001
        # L'avviso e' un di piu': se non parte, l'operazione resta fatta e a
        # registro. Sollevare qui farebbe credere a chi ha risposto che la
        # risposta non sia partita.
        logger.exception("Avviso per posta non inviato")
        return 0
    return len(destinatari)


def pratica_evasa(azione: str, mittente: str, oggetto: str,
                  operatore: str, dettaglio: str = "") -> Dict[str, int]:
    """Dice a chi segue la casella che una pratica e' stata chiusa."""
    righe: List[str] = [
        f"{azione} da {operatore}.",
        "",
        f"Messaggio di: {mittente}",
        f"Oggetto: {oggetto or '(senza oggetto)'}",
    ]
    if dettaglio:
        righe += ["", dettaglio]
    righe += [
        "",
        "Avviso automatico della pagina di gestione della posta.",
        "Sperimentazione trasporto pubblico a guida autonoma - Comune di Imperia",
    ]
    corpo = "\n".join(righe)

    testo_telegram = (
        f"\U0001f4ec <b>Casella TPL</b> — {_html.escape(azione.lower())}\n\n"
        f"Da: {_html.escape(mittente)}\n"
        f"Oggetto: {_html.escape(oggetto or '(senza oggetto)')}\n"
        f"Operatore: {_html.escape(operatore)}"
    )
    if dettaglio:
        testo_telegram += f"\n\n<i>{_html.escape(dettaglio)}</i>"

    quanti_email = _email(f"Casella TPL - {azione.lower()}", corpo)
    quanti_telegram = _telegram(testo_telegram)
    logger.info("Avviso di pratica evasa",
                extra={"context": {"azione": azione, "email": quanti_email,
                                   "telegram": quanti_telegram}})
    return {"email": quanti_email, "telegram": quanti_telegram}
