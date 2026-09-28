"""Lettura della casella della sperimentazione e risposta ai cittadini.

La sorveglianza oraria avvisa che è arrivato qualcosa; questo modulo serve a
**farci qualcosa**: leggere il messaggio, rispondere, e segnare la pratica come
chiusa senza uscire dall'applicazione.

La casella è la stessa da cui partono le comunicazioni del servizio
(``SMTP_USER``), quindi la risposta arriva al cittadino dall'indirizzo a cui
aveva scritto, che è l'unica cosa sensata: una risposta che arriva da un altro
mittente sembra spam, e nessuno la legge.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import logging
import os
import re
from dataclasses import dataclass, field
from email.utils import parseaddr, parsedate_to_datetime
from typing import List, Optional

logger = logging.getLogger("tpl.posta_in_arrivo")

CARTELLA = "INBOX"
CESTINO = "Trash"

# Mittenti che non sono cittadini: avvisi di mancata consegna e notifiche
# automatiche. Restano visibili, ma segnati, perche' a un rimbalzo non si
# risponde — e perche' un rimbalzo che si ripete e' esso stesso una
# segnalazione: vuol dire che scriviamo a un indirizzo che non esiste.
AUTOMATICI = (
    "mailer-daemon@",
    "postmaster@",
    "noreply@",
    "no-reply@",
    "notifications@",
)

ANTEPRIMA = 400


class CasellaNonConfigurata(RuntimeError):
    """Mancano le credenziali di lettura: la pagina lo dice invece di rompersi."""


@dataclass
class Messaggio:
    uid: str
    quando: Optional[object]
    mittente: str
    indirizzo: str
    oggetto: str
    anteprima: str = ""
    testo: str = ""
    letto: bool = False
    risposto: bool = False
    automatico: bool = False
    message_id: str = ""
    riferimenti: List[str] = field(default_factory=list)


def configurata() -> bool:
    return all(os.environ.get(v) for v in
               ("TPL_IMAP_HOST", "TPL_IMAP_USER", "TPL_IMAP_PASSWORD"))


def _connessione() -> imaplib.IMAP4_SSL:
    if not configurata():
        raise CasellaNonConfigurata(
            "TPL_IMAP_HOST, TPL_IMAP_USER e TPL_IMAP_PASSWORD non sono impostati")
    conn = imaplib.IMAP4_SSL(os.environ["TPL_IMAP_HOST"],
                             int(os.environ.get("TPL_IMAP_PORT", "993")))
    conn.login(os.environ["TPL_IMAP_USER"], os.environ["TPL_IMAP_PASSWORD"])
    return conn


def _decodifica(valore: Optional[str]) -> str:
    if not valore:
        return ""
    try:
        return str(email.header.make_header(email.header.decode_header(valore)))
    except (UnicodeDecodeError, LookupError, ValueError):
        return valore


def _testo(messaggio) -> str:
    """Il corpo in testo semplice, ripulito di cio' che non l'ha scritto nessuno.

    I filtri antispam del Comune antepongono al corpo un avviso sul mittente
    insolito, e in coda aggiungono la propria firma: mostrarli confonderebbe
    chi legge la pratica, perche' sembrano parole del cittadino.
    """
    parte = messaggio.get_body(preferencelist=("plain",))
    if parte is None:
        parte = messaggio.get_body(preferencelist=("html",))
        if parte is None:
            return ""
        grezzo = re.sub(r"<[^>]+>", " ", parte.get_content())
    else:
        grezzo = parte.get_content()

    righe = []
    for riga in grezzo.splitlines():
        nuda = riga.strip()
        if nuda.startswith("Attenzione:") and "mittente" in nuda.lower():
            continue
        if nuda.startswith("Attenzione:") and "fidi" in nuda.lower():
            continue
        if nuda.startswith("Messaggio analizzato da"):
            continue
        righe.append(riga.rstrip())

    # via le righe vuote in testa e in coda
    while righe and not righe[0].strip():
        righe.pop(0)
    while righe and not righe[-1].strip():
        righe.pop()
    if righe and righe[-1].strip() == "--":
        righe.pop()
    return "\n".join(righe)


def elenco(limite: int = 50, con_testo: bool = False) -> List[Messaggio]:
    """I messaggi in casella, dal piu' recente."""
    conn = _connessione()
    try:
        conn.select(CARTELLA)
        esito, dati = conn.uid("search", None, "ALL")
        if esito != "OK":
            return []
        uid_tutti = dati[0].split()
        scelti = uid_tutti[-limite:][::-1]
        messaggi = []
        for uid in scelti:
            pezzi = "(RFC822)" if con_testo else "(FLAGS BODY.PEEK[])"
            esito, dati = conn.uid("fetch", uid, pezzi)
            if esito != "OK" or not dati or not isinstance(dati[0], tuple):
                continue
            grezzo = dati[0][1]
            bandiere = str(dati[0][0])
            msg = email.message_from_bytes(grezzo, policy=email.policy.default)

            nome, indirizzo = parseaddr(_decodifica(msg.get("From")))
            try:
                quando = parsedate_to_datetime(msg.get("Date"))
            except (TypeError, ValueError):
                quando = None

            testo = _testo(msg)
            messaggi.append(Messaggio(
                uid=uid.decode(),
                quando=quando,
                mittente=nome or indirizzo,
                indirizzo=indirizzo.lower(),
                oggetto=_decodifica(msg.get("Subject")),
                anteprima=" ".join(testo.split())[:ANTEPRIMA],
                testo=testo if con_testo else "",
                letto="\\Seen" in bandiere,
                risposto="\\Answered" in bandiere,
                automatico=any(a in indirizzo.lower() for a in AUTOMATICI),
                message_id=(msg.get("Message-ID") or "").strip(),
                riferimenti=(msg.get("References") or "").split(),
            ))
        return messaggi
    finally:
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def leggi(uid: str) -> Optional[Messaggio]:
    for m in elenco(limite=200, con_testo=True):
        if m.uid == uid:
            return m
    return None


def segna_risposto(uid: str) -> None:
    """Mette la bandiera \\Answered sull'originale.

    Serve a chi apre la casella da un client di posta: senza, due persone
    rispondono allo stesso cittadino senza sapere l'una dell'altra.
    """
    conn = _connessione()
    try:
        conn.select(CARTELLA)
        conn.uid("store", uid, "+FLAGS", "(\\Answered \\Seen)")
    finally:
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def elimina(uid: str) -> str:
    """Sposta il messaggio nel cestino della casella.

    Non lo distrugge: lo copia in ``Trash`` e lo toglie dalla posta in arrivo.
    Su una casella istituzionale un messaggio e' corrispondenza con un
    cittadino, e cancellarlo davvero non e' una decisione da prendere con un
    pulsante — nel cestino resta recuperabile da qualunque programma di posta.

    Lo spurgo e' mirato al singolo messaggio (``UID EXPUNGE``, disponibile
    perche' il server dichiara UIDPLUS): un ``EXPUNGE`` semplice porterebbe
    via **tutti** i messaggi marcati per la cancellazione nella cartella,
    compresi quelli marcati da qualcun altro che in quel momento sta lavorando
    dal suo programma di posta.
    """
    conn = _connessione()
    try:
        conn.select(CARTELLA)
        esito, _ = conn.uid("copy", uid, CESTINO)
        if esito != "OK":
            raise RuntimeError(
                f"non si riesce a copiare il messaggio in {CESTINO}")
        conn.uid("store", uid, "+FLAGS", "(\\Deleted)")
        if "UIDPLUS" in conn.capabilities:
            conn.uid("expunge", uid)
        else:
            # Senza UIDPLUS non si spurga: meglio lasciarlo marcato e sparire
            # dalla vista che rischiare di portarsi via i messaggi altrui.
            logger.warning("UID EXPUNGE non disponibile: messaggio solo marcato")
        logger.info("Messaggio spostato nel cestino",
                    extra={"context": {"uid": uid}})
        return CESTINO
    finally:
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def rispondi(messaggio: Messaggio, testo: str) -> None:
    """Invia la risposta al cittadino e segna l'originale come evaso."""
    from . import posta

    oggetto = messaggio.oggetto or "La sua richiesta"
    if not oggetto.lower().startswith("re:"):
        oggetto = f"Re: {oggetto}"

    posta.invia(
        destinatari=[messaggio.indirizzo],
        oggetto=oggetto,
        corpo=testo,
        # Il messaggio si aggancia alla conversazione: nel client del cittadino
        # la risposta finisce sotto la sua domanda, non in un filo a parte.
        riferimento=messaggio.message_id,
    )
    segna_risposto(messaggio.uid)
    logger.info("Risposta inviata a un messaggio in casella",
                extra={"context": {"uid": messaggio.uid,
                                   "oggetto": messaggio.oggetto}})
