"""Stato della sorveglianza della casella della sperimentazione.

La sorveglianza vera e propria e' un programma a se' (`tpl-inbox-watch`), che
gira a cadenza oraria e scrive un file di stato: da qui lo si legge soltanto,
cosi' l'interfaccia non puo' disallineare il cursore dei messaggi gia'
notificati. La pagina mostra quando e' passato l'ultimo controllo e che cosa
e' stato segnalato.

Si scrive invece l'elenco dei destinatari degli avvisi, tenuto in un file a
parte: chi risponde ai cittadini cambia nel tempo e deve poterlo aggiornare
senza mettere mano alla configurazione del server.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

# Ogni quanto gira la sorveglianza: serve solo per dire se un silenzio e'
# normale o se il programma si e' fermato.
PERIODO_ATTESO = timedelta(hours=1)
# Oltre questo margine il ritardo diventa un'anomalia da segnalare.
TOLLERANZA = timedelta(minutes=20)


def percorso_stato() -> Path:
    return Path(
        os.environ.get(
            "TPL_INBOX_STATE_FILE", "/var/lib/tpl-inbox-watch/stato.json"
        )
    )


def _quando(valore: str):
    try:
        letta = datetime.fromisoformat(valore)
    except (TypeError, ValueError):
        return None
    return letta if letta.tzinfo else letta.replace(tzinfo=timezone.utc)


def leggi_stato() -> Dict[str, Any]:
    """Stato della sorveglianza, con il giudizio su quanto e' aggiornato.

    Le chiavi `disponibile` e `situazione` sono sempre presenti: la pagina deve
    poter dire "non lo so" senza sollevare eccezioni.
    """
    percorso = percorso_stato()

    if not percorso.exists():
        return {
            "disponibile": False,
            "situazione": "assente",
            "percorso": str(percorso),
        }

    try:
        dati = json.loads(percorso.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, PermissionError) as errore:
        return {
            "disponibile": False,
            "situazione": "illeggibile",
            "percorso": str(percorso),
            "motivo": type(errore).__name__,
        }

    ultimo = _quando(dati.get("ultimo_controllo", ""))
    adesso = datetime.now(timezone.utc)

    if ultimo is None:
        situazione = "sconosciuta"
        ritardo = None
    else:
        ritardo = adesso - ultimo
        situazione = "attiva" if ritardo <= PERIODO_ATTESO + TOLLERANZA else "ferma"

    configurazione = dati.get("configurazione", {})
    return {
        "disponibile": True,
        "situazione": situazione,
        "percorso": str(percorso),
        "ultimo_controllo": ultimo,
        "ritardo_minuti": int(ritardo.total_seconds() // 60) if ritardo else None,
        "ultima_notifica": _quando(dati.get("ultima_notifica", "")),
        "ultimi_notificati": dati.get("ultimi_notificati", 0),
        "ultimi_messaggi": dati.get("ultimi_messaggi", []),
        "ultimo_uid": dati.get("ultimo_uid"),
        "casella": configurazione.get("casella"),
        "destinatari": configurazione.get("destinatari", []),
        "copia": configurazione.get("copia", []),
        "telegram": configurazione.get("telegram", 0),
        "anteprima": configurazione.get("anteprima"),
    }


# --------------------------------------------------------- destinatari
# I destinatari degli avvisi stanno in un file a parte, non nella
# configurazione del programma di sorveglianza: quella contiene le credenziali
# della casella e non puo' essere esposta a un'interfaccia web. Qui viaggiano
# solo indirizzi e identificativi di chat, che l'applicazione scrive e la
# sorveglianza rilegge a ogni giro.

RUOLI_EMAIL = ("a", "cc")


def percorso_destinatari() -> Path:
    return Path(
        os.environ.get(
            "TPL_INBOX_DESTINATARI",
            str(percorso_stato().parent / "destinatari.json"),
        )
    )


def _vuoti() -> Dict[str, Any]:
    return {"email": [], "telegram": []}


def leggi_destinatari() -> Dict[str, Any]:
    """Destinatari configurati. File assente o illeggibile: elenchi vuoti."""
    percorso = percorso_destinatari()
    if not percorso.exists():
        return _vuoti()
    try:
        dati = json.loads(percorso.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, PermissionError):
        return _vuoti()

    return {
        "email": [v for v in dati.get("email", []) if v.get("indirizzo")],
        "telegram": [v for v in dati.get("telegram", []) if v.get("chat_id")],
    }


def _scrivi(dati: Dict[str, Any]) -> None:
    percorso = percorso_destinatari()
    percorso.parent.mkdir(parents=True, exist_ok=True)
    tmp = percorso.with_suffix(".tmp")
    tmp.write_text(json.dumps(dati, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(percorso)
    # Il file lo scrive l'applicazione ma lo rilegge la sorveglianza, che gira
    # con un'altra utenza: senza lettura al gruppo resterebbe inservibile.
    try:
        percorso.chmod(0o664)
    except OSError:
        pass


def _pulisci_indirizzo(valore: str) -> str:
    return (valore or "").strip().lower()


def indirizzo_valido(valore: str) -> bool:
    """Controllo volutamente permissivo: qui l'errore tipico e' la distrazione,
    non l'indirizzo esotico. La verifica vera la fa il server di posta."""
    valore = _pulisci_indirizzo(valore)
    if valore.count("@") != 1 or " " in valore:
        return False
    locale, dominio = valore.split("@")
    return bool(locale) and "." in dominio and not dominio.startswith(".")


def aggiungi_email(indirizzo: str, ruolo: str = "a", nota: str = "") -> str:
    """Aggiunge un destinatario di posta. Ritorna il messaggio per l'operatore."""
    indirizzo = _pulisci_indirizzo(indirizzo)
    if not indirizzo_valido(indirizzo):
        raise ValueError("Indirizzo non valido.")
    if ruolo not in RUOLI_EMAIL:
        raise ValueError("Ruolo non riconosciuto.")

    dati = leggi_destinatari()
    if any(v["indirizzo"] == indirizzo for v in dati["email"]):
        raise ValueError("Questo indirizzo e' gia' fra i destinatari.")

    dati["email"].append(
        {"indirizzo": indirizzo, "ruolo": ruolo, "nota": nota.strip()[:120]}
    )
    _scrivi(dati)
    return f"Aggiunto {indirizzo}."


def modifica_email(indirizzo: str, ruolo: str, nota: str = "") -> str:
    indirizzo = _pulisci_indirizzo(indirizzo)
    if ruolo not in RUOLI_EMAIL:
        raise ValueError("Ruolo non riconosciuto.")

    dati = leggi_destinatari()
    for voce in dati["email"]:
        if voce["indirizzo"] == indirizzo:
            voce["ruolo"] = ruolo
            voce["nota"] = nota.strip()[:120]
            _scrivi(dati)
            return f"Aggiornato {indirizzo}."
    raise ValueError("Destinatario non trovato.")


def elimina_email(indirizzo: str) -> str:
    indirizzo = _pulisci_indirizzo(indirizzo)
    dati = leggi_destinatari()
    restanti = [v for v in dati["email"] if v["indirizzo"] != indirizzo]
    if len(restanti) == len(dati["email"]):
        raise ValueError("Destinatario non trovato.")
    if not any(v["ruolo"] == "a" for v in restanti):
        raise ValueError(
            "Deve restare almeno un destinatario principale: senza, gli avvisi "
            "non arriverebbero a nessuno."
        )
    dati["email"] = restanti
    _scrivi(dati)
    return f"Rimosso {indirizzo}."


def aggiungi_telegram(chat_id: str, nome: str = "") -> str:
    chat_id = (chat_id or "").strip()
    if not re.fullmatch(r"-?\d{5,20}", chat_id):
        raise ValueError(
            "L'identificativo Telegram e' un numero: si ottiene facendo "
            "scrivere al bot la persona da abilitare."
        )
    dati = leggi_destinatari()
    if any(v["chat_id"] == chat_id for v in dati["telegram"]):
        raise ValueError("Questo destinatario Telegram e' gia' presente.")
    dati["telegram"].append({"chat_id": chat_id, "nome": nome.strip()[:80]})
    _scrivi(dati)
    return f"Aggiunto {nome.strip() or chat_id} su Telegram."


def modifica_telegram(chat_id: str, nome: str) -> str:
    chat_id = (chat_id or "").strip()
    dati = leggi_destinatari()
    for voce in dati["telegram"]:
        if voce["chat_id"] == chat_id:
            voce["nome"] = nome.strip()[:80]
            _scrivi(dati)
            return "Nome aggiornato."
    raise ValueError("Destinatario non trovato.")


def elimina_telegram(chat_id: str) -> str:
    chat_id = (chat_id or "").strip()
    dati = leggi_destinatari()
    restanti = [v for v in dati["telegram"] if v["chat_id"] != chat_id]
    if len(restanti) == len(dati["telegram"]):
        raise ValueError("Destinatario non trovato.")
    dati["telegram"] = restanti
    _scrivi(dati)
    return "Destinatario Telegram rimosso."


def nome_bot() -> str:
    """Bot Telegram del progetto, senza la chiocciola.

    Serve alla pagina per dire *a chi* deve scrivere la persona da abilitare:
    l'identificativo di chat esiste solo dopo il primo messaggio al bot.
    """
    return os.environ.get("TPL_TELEGRAM_BOT", "tpl_imperia_bot").lstrip("@").strip()
