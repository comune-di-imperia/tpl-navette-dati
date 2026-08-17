"""Invio email: report di elaborazione e messaggi legati all'utenza."""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional

MITTENTE = "TPL Navette Imperia"


class PostaNonConfigurata(RuntimeError):
    """Manca la configurazione SMTP: senza, il recupero password non funziona."""


def configurata() -> bool:
    return all(os.environ.get(v) for v in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"))


def base_url() -> str:
    """Indirizzo pubblico dell'applicazione.

    Deve venire dalla configurazione e MAI dall'intestazione ``Host`` della
    richiesta: chi la controlla potrebbe far generare un link di recupero che
    punta al proprio dominio e intercettare il token.
    """
    return os.environ.get("TPL_BASE_URL", "https://tpl.comune.imperia.it").rstrip("/")


def invia(
    destinatari: List[str],
    oggetto: str,
    corpo: str,
    allegati: Optional[List[Path]] = None,
) -> None:
    """Porta 587 con STARTTLS: la 25 e' bloccata in uscita."""
    if not configurata():
        raise PostaNonConfigurata("SMTP_HOST, SMTP_USER e SMTP_PASS non sono impostati")

    msg = EmailMessage()
    msg["From"] = f"{MITTENTE} <{os.environ['SMTP_USER']}>"
    msg["To"] = ", ".join(destinatari)
    msg["Subject"] = oggetto
    msg.set_content(corpo)

    for percorso in allegati or []:
        p = Path(percorso)
        msg.add_attachment(
            p.read_bytes(),
            maintype="application",
            subtype="octet-stream",
            filename=p.name,
        )

    with smtplib.SMTP(
        os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=60
    ) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)


def invia_link_password(email: str, token: str, tipo: str, nome: str = "") -> None:
    """Recapita il link di primo accesso o di recupero password.

    Il token compare solo nel corpo, mai nell'oggetto, e non va scritto nei log.
    """
    saluto = f"Buongiorno {nome}," if nome else "Buongiorno,"
    link = f"{base_url()}/password/reimposta/{token}"

    if tipo == "primo_accesso":
        oggetto = "Attivazione dell'utenza - TPL navette Imperia"
        testo = (
            f"{saluto}\n\n"
            "e' stata creata un'utenza a suo nome per l'applicazione di gestione\n"
            "dati delle navette a guida autonoma del Comune di Imperia.\n\n"
            "Per scegliere la password e attivare l'accesso:\n\n"
            f"    {link}\n\n"
            "Il collegamento e' valido 48 ore e puo' essere usato una sola volta.\n\n"
            "Se ritiene di aver ricevuto questo messaggio per errore, lo ignori:\n"
            "senza attivazione l'utenza resta inutilizzabile.\n"
        )
    else:
        oggetto = "Recupero password - TPL navette Imperia"
        testo = (
            f"{saluto}\n\n"
            "abbiamo ricevuto una richiesta di reimpostazione della password\n"
            "per la sua utenza. Per scegliere una nuova password:\n\n"
            f"    {link}\n\n"
            "Il collegamento e' valido 30 minuti e puo' essere usato una sola volta.\n\n"
            "Se non ha richiesto lei il recupero, ignori questo messaggio: la\n"
            "password attuale resta valida. Se il caso si ripete, lo segnali\n"
            "all'amministratore del sistema.\n"
        )
    invia([email], oggetto, testo)


def avvisa_cambio_password(email: str, nome: str = "") -> None:
    """Notifica di avvenuto cambio password.

    Va inviata SEMPRE, anche quando il cambio e' legittimo: e' l'unico modo in
    cui il titolare si accorge di una modifica che non ha richiesto.
    """
    saluto = f"Buongiorno {nome}," if nome else "Buongiorno,"
    invia(
        [email],
        "Password modificata - TPL navette Imperia",
        f"{saluto}\n\n"
        "la password della sua utenza e' stata modificata poco fa e tutte le\n"
        "sessioni aperte sono state chiuse.\n\n"
        "Se non e' stato lei, contatti subito l'amministratore del sistema:\n"
        "qualcuno potrebbe avere accesso alla sua casella di posta.\n",
    )
