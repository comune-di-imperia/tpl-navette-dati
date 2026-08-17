"""Persistenza dell'applicazione TPL: utenti, elaborazioni e registro attivita'.

SQLite: i volumi sono modesti (poche decine di caricamenti al giorno) e non
richiede un servizio a parte da presidiare sul VPS.

Le password sono salvate con PBKDF2-SHA256 e sale per utente. Lo schema tiene
gia' le colonne del secondo fattore, cosi' attivarlo non comportera' una
migrazione dei dati.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PERCORSO_DB = Path(os.environ.get("TPL_DB", "/var/lib/tpl-navette/tpl.sqlite"))

ITERAZIONI_PBKDF2 = 480_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS utenti (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    utente        TEXT NOT NULL UNIQUE,
    nome          TEXT NOT NULL DEFAULT '',
    email         TEXT NOT NULL DEFAULT '',
    hash_password TEXT NOT NULL,
    sale          TEXT NOT NULL,
    ruolo         TEXT NOT NULL DEFAULT 'operatore',   -- operatore | amministratore
    attivo        INTEGER NOT NULL DEFAULT 1,
    totp_segreto  TEXT,            -- secondo fattore, previsto ma non attivo
    totp_attivo   INTEGER NOT NULL DEFAULT 0,
    creato_il     TEXT NOT NULL,
    ultimo_accesso TEXT
);

CREATE TABLE IF NOT EXISTS elaborazioni (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    utente_id     INTEGER REFERENCES utenti(id),
    operatore     TEXT NOT NULL DEFAULT '',   -- nome utente, o indirizzo IP
    nome_file     TEXT NOT NULL,
    dimensione    INTEGER NOT NULL DEFAULT 0,
    navetta       TEXT NOT NULL DEFAULT '',
    vin           TEXT NOT NULL DEFAULT '',
    data_dati     TEXT NOT NULL DEFAULT '',
    stato         TEXT NOT NULL DEFAULT 'in_corso',  -- in_corso|completata|fallita
    messaggio     TEXT NOT NULL DEFAULT '',
    riepilogo     TEXT NOT NULL DEFAULT '{}',
    chiave_s3     TEXT NOT NULL DEFAULT '',
    pdf           TEXT NOT NULL DEFAULT '',
    iniziata_il   TEXT NOT NULL,
    conclusa_il   TEXT
);

CREATE TABLE IF NOT EXISTS registro (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    quando        TEXT NOT NULL,
    utente        TEXT NOT NULL DEFAULT '',
    azione        TEXT NOT NULL,
    esito         TEXT NOT NULL DEFAULT 'ok',
    dettaglio     TEXT NOT NULL DEFAULT '',
    indirizzo_ip  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_registro_quando ON registro(quando DESC);
CREATE INDEX IF NOT EXISTS idx_elab_iniziata ON elaborazioni(iniziata_il DESC);
"""


def _adesso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connessione():
    PERCORSO_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(PERCORSO_DB), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def inizializza() -> None:
    with connessione() as con:
        con.executescript(SCHEMA)
        # colonna introdotta dopo la messa in esercizio: i primi caricamenti
        # sono gia' nel database e non vanno persi
        colonne = {r["name"] for r in con.execute("PRAGMA table_info(elaborazioni)")}
        if "operatore" not in colonne:
            con.execute(
                "ALTER TABLE elaborazioni ADD COLUMN operatore TEXT NOT NULL DEFAULT ''"
            )


# ----------------------------------------------------------------- utenti
def _impronta(password: str, sale: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(sale), ITERAZIONI_PBKDF2
    ).hex()


def crea_utente(
    utente: str,
    password: str,
    nome: str = "",
    email: str = "",
    ruolo: str = "operatore",
) -> int:
    sale = secrets.token_hex(16)
    with connessione() as con:
        cur = con.execute(
            "INSERT INTO utenti (utente, nome, email, hash_password, sale, ruolo, "
            "creato_il) VALUES (?,?,?,?,?,?,?)",
            (utente, nome, email, _impronta(password, sale), sale, ruolo, _adesso()),
        )
        return cur.lastrowid


def cambia_password(utente: str, password: str) -> bool:
    sale = secrets.token_hex(16)
    with connessione() as con:
        cur = con.execute(
            "UPDATE utenti SET hash_password=?, sale=? WHERE utente=?",
            (_impronta(password, sale), sale, utente),
        )
        return cur.rowcount > 0


def verifica_credenziali(utente: str, password: str) -> Optional[Dict[str, Any]]:
    """Ritorna l'utente se le credenziali sono valide e l'account e' attivo."""
    with connessione() as con:
        r = con.execute(
            "SELECT * FROM utenti WHERE utente=? AND attivo=1", (utente,)
        ).fetchone()
        if not r:
            # confronto comunque, per non rivelare dal tempo di risposta se
            # l'utenza esista
            _impronta(password, secrets.token_hex(16))
            return None
        atteso = r["hash_password"]
        if not hmac.compare_digest(_impronta(password, r["sale"]), atteso):
            return None
        con.execute(
            "UPDATE utenti SET ultimo_accesso=? WHERE id=?", (_adesso(), r["id"])
        )
        return dict(r)


def esistono_utenti() -> bool:
    with connessione() as con:
        return bool(
            con.execute("SELECT 1 FROM utenti WHERE attivo=1 LIMIT 1").fetchone()
        )


def elenco_utenti() -> List[Dict[str, Any]]:
    with connessione() as con:
        return [
            dict(r)
            for r in con.execute(
                "SELECT id, utente, nome, email, ruolo, attivo, totp_attivo, "
                "creato_il, ultimo_accesso FROM utenti ORDER BY utente"
            )
        ]


# ----------------------------------------------------- registro e lavorazioni
def registra(
    azione: str,
    utente: str = "",
    esito: str = "ok",
    dettaglio: str = "",
    indirizzo_ip: str = "",
) -> None:
    """Annota un'azione nel registro. Non solleva mai: il log non deve
    impedire l'operazione che stava tracciando."""
    try:
        with connessione() as con:
            con.execute(
                "INSERT INTO registro (quando, utente, azione, esito, dettaglio, "
                "indirizzo_ip) VALUES (?,?,?,?,?,?)",
                (_adesso(), utente, azione, esito, dettaglio[:2000], indirizzo_ip),
            )
    except sqlite3.Error:
        pass


def leggi_registro(limite: int = 200, utente: str = "") -> List[Dict[str, Any]]:
    sql = "SELECT * FROM registro"
    par: List[Any] = []
    if utente:
        sql += " WHERE utente=?"
        par.append(utente)
    sql += " ORDER BY quando DESC, id DESC LIMIT ?"
    par.append(limite)
    with connessione() as con:
        return [dict(r) for r in con.execute(sql, par)]


def apri_elaborazione(
    utente_id: Optional[int], nome_file: str, dimensione: int, operatore: str = ""
) -> int:
    with connessione() as con:
        cur = con.execute(
            "INSERT INTO elaborazioni (utente_id, operatore, nome_file, dimensione, "
            "iniziata_il) VALUES (?,?,?,?,?)",
            (utente_id, operatore, nome_file, dimensione, _adesso()),
        )
        return cur.lastrowid


def chiudi_elaborazione(
    elab_id: int,
    stato: str,
    messaggio: str = "",
    riepilogo: Optional[Dict] = None,
    chiave_s3: str = "",
    navetta: str = "",
    vin: str = "",
    data_dati: str = "",
    pdf: str = "",
) -> None:
    with connessione() as con:
        con.execute(
            "UPDATE elaborazioni SET stato=?, messaggio=?, riepilogo=?, chiave_s3=?, "
            "navetta=?, vin=?, data_dati=?, pdf=?, conclusa_il=? WHERE id=?",
            (
                stato,
                messaggio[:2000],
                json.dumps(riepilogo or {}, default=str),
                chiave_s3,
                navetta,
                vin,
                data_dati,
                pdf,
                _adesso(),
                elab_id,
            ),
        )


def leggi_elaborazione(elab_id: int) -> Optional[Dict[str, Any]]:
    with connessione() as con:
        r = con.execute(
            "SELECT e.*, u.utente FROM elaborazioni e LEFT JOIN utenti u "
            "ON u.id=e.utente_id WHERE e.id=?",
            (elab_id,),
        ).fetchone()
        return dict(r) if r else None


def elenco_elaborazioni(limite: int = 100, solo_con_report: bool = False):
    dove = "WHERE e.pdf <> ''" if solo_con_report else ""
    with connessione() as con:
        return [
            dict(r)
            for r in con.execute(
                "SELECT e.*, u.utente FROM elaborazioni e LEFT JOIN utenti u "
                f"ON u.id=e.utente_id {dove} "
                "ORDER BY e.iniziata_il DESC, e.id DESC LIMIT ?",
                (limite,),
            )
        ]
