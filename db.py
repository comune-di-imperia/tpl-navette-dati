"""Persistenza dell'applicazione TPL: utenti, ruoli, token, elaborazioni, registro.

SQLite: i volumi sono modesti (poche decine di caricamenti al giorno) e non
richiede un servizio a parte da presidiare sul VPS.

Le password non sono salvate: si conserva l'esito di una derivazione lenta in un
formato che si autodescrive (vedi :func:`cifra_password`), cosi' alzare il costo
o cambiare algoritmo non invalida le password esistenti.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .permessi import AMMINISTRATORE, RUOLI

PERCORSO_DB = Path(os.environ.get("TPL_DB", "/var/lib/tpl-navette/tpl.sqlite"))

# OWASP raccomanda 600.000 iterazioni per PBKDF2-HMAC-SHA256. Alzando questo
# numero le impronte gia' salvate restano valide e vengono ricalcolate al primo
# accesso riuscito di ciascun utente.
ITERAZIONI_PBKDF2 = 600_000

# Tentativi consecutivi falliti prima del blocco temporaneo. Il blocco e' sempre
# a termine: se fosse definitivo, chi conosce uno username potrebbe escludere
# chiunque dal sistema a piacere.
TENTATIVI_MASSIMI = 5
BLOCCO_MINUTI = 15

DURATA_TOKEN_RESET_MIN = 30
DURATA_TOKEN_PRIMO_ACCESSO_ORE = 48

STATI = ("attivo", "sospeso", "archiviato")


class UltimoAmministratore(Exception):
    """Il sistema resterebbe senza amministratori attivi."""


class UtenteDuplicato(Exception):
    """Nome utente o email gia' assegnati."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS ruoli (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    codice      TEXT NOT NULL UNIQUE,
    nome        TEXT NOT NULL,
    descrizione TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS utenti (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    utente         TEXT NOT NULL UNIQUE,
    email          TEXT NOT NULL UNIQUE,
    nome           TEXT NOT NULL DEFAULT '',
    ruolo_id       INTEGER NOT NULL REFERENCES ruoli(id),

    hash_password  TEXT NOT NULL DEFAULT '',   -- vuoto = primo accesso da fare
    stato          TEXT NOT NULL DEFAULT 'attivo',

    tentativi_falliti INTEGER NOT NULL DEFAULT 0,
    bloccato_fino     TEXT,

    -- incrementata per buttare fuori tutte le sessioni aperte dell'utente
    epoca_sessione INTEGER NOT NULL DEFAULT 1,
    deve_cambiare_password INTEGER NOT NULL DEFAULT 0,

    totp_segreto   TEXT,             -- secondo fattore, previsto ma non attivo
    totp_attivo    INTEGER NOT NULL DEFAULT 0,

    creato_il      TEXT NOT NULL,
    creato_da      INTEGER REFERENCES utenti(id),   -- nullo per il primo utente
    modificato_il  TEXT,
    ultimo_accesso TEXT
);

CREATE INDEX IF NOT EXISTS idx_utenti_stato ON utenti(stato);

CREATE TABLE IF NOT EXISTS token_accesso (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    utente_id     INTEGER NOT NULL REFERENCES utenti(id) ON DELETE CASCADE,
    tipo          TEXT NOT NULL DEFAULT 'reset',   -- reset | primo_accesso

    -- MAI il token in chiaro: chi legge la tabella non deve poterne fare nulla
    hash_token    TEXT NOT NULL UNIQUE,

    creato_il     TEXT NOT NULL,
    scade_il      TEXT NOT NULL,
    usato_il      TEXT,
    invalidato_il TEXT,
    ip_richiesta  TEXT NOT NULL DEFAULT '',
    agente        TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_token_utente ON token_accesso(utente_id);
CREATE INDEX IF NOT EXISTS idx_token_scadenza ON token_accesso(scade_il);

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
    bersaglio     TEXT NOT NULL DEFAULT '',   -- su CHI si e' agito
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


def _fra(**kw) -> str:
    return (datetime.now(timezone.utc) + timedelta(**kw)).isoformat(timespec="seconds")


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


# ------------------------------------------------------------------ migrazioni
def _colonne(con, tabella: str) -> set:
    return {r["name"] for r in con.execute(f"PRAGMA table_info({tabella})")}


def _colonne_mancanti(con) -> None:
    """Colonne introdotte dopo la messa in esercizio, aggiunte in loco."""
    if "operatore" not in _colonne(con, "elaborazioni"):
        con.execute(
            "ALTER TABLE elaborazioni ADD COLUMN operatore TEXT NOT NULL DEFAULT ''"
        )
    if "pdf" not in _colonne(con, "elaborazioni"):
        con.execute("ALTER TABLE elaborazioni ADD COLUMN pdf TEXT NOT NULL DEFAULT ''")
    if "bersaglio" not in _colonne(con, "registro"):
        con.execute(
            "ALTER TABLE registro ADD COLUMN bersaglio TEXT NOT NULL DEFAULT ''"
        )


def _copia_utenti(con) -> None:
    """Travasa le utenze dal vecchio schema: ruolo testuale, sale separato,
    ``attivo`` booleano."""
    corrispondenza = {"amministratore": "amministratore", "operatore": "tecnico"}
    for v in con.execute("SELECT * FROM utenti_vecchia"):
        ruolo = corrispondenza.get(v["ruolo"], "tecnico")
        # sale e impronta separati diventano il formato autodescrittivo
        impronta = (
            f"pbkdf2_sha256$480000${v['sale']}${v['hash_password']}"
            if v["hash_password"]
            else ""
        )
        con.execute(
            "INSERT INTO utenti (utente, email, nome, ruolo_id, hash_password, "
            "stato, totp_segreto, totp_attivo, creato_il, ultimo_accesso) "
            "VALUES (?,?,?,(SELECT id FROM ruoli WHERE codice=?),?,?,?,?,?,?)",
            (
                v["utente"],
                # l'email diventa obbligatoria: senza, il recupero password non
                # potrebbe funzionare. Il segnaposto e' volutamente non
                # recapitabile, cosi' l'amministratore se ne accorge.
                v["email"] or f"{v['utente']}@non-impostata.invalid",
                v["nome"],
                ruolo,
                impronta,
                "attivo" if v["attivo"] else "sospeso",
                v["totp_segreto"],
                v["totp_attivo"],
                v["creato_il"],
                v["ultimo_accesso"],
            ),
        )
    con.execute("DROP TABLE utenti_vecchia")


def _semina_ruoli(con) -> None:
    # "amministrativo" e' diventato "consultazione": si rinomina la riga
    # esistente invece di aggiungerne una nuova, cosi' gli utenti che vi
    # puntano restano collegati senza toccare ruolo_id
    con.execute(
        "UPDATE ruoli SET codice='consultazione' WHERE codice='amministrativo' "
        "AND NOT EXISTS (SELECT 1 FROM ruoli WHERE codice='consultazione')"
    )
    for codice, nome, descrizione in RUOLI:
        con.execute(
            "INSERT INTO ruoli (codice, nome, descrizione) VALUES (?,?,?) "
            "ON CONFLICT(codice) DO UPDATE SET nome=excluded.nome, "
            "descrizione=excluded.descrizione",
            (codice, nome, descrizione),
        )


def inizializza() -> None:
    """Crea lo schema o lo porta alla forma attuale. Idempotente."""
    with connessione() as con:
        # Durante la migrazione le chiavi esterne vanno spente e il RENAME deve
        # restare "legacy": altrimenti SQLite riscrive i riferimenti di
        # elaborazioni facendoli puntare alla tabella temporanea, che poi
        # eliminiamo, lasciando un vincolo appeso nel vuoto.
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("PRAGMA legacy_alter_table=ON")

        # la tabella utenti va rinominata PRIMA di applicare lo schema nuovo:
        # CREATE TABLE IF NOT EXISTS la lascerebbe intatta e la creazione
        # dell'indice su una colonna che non ha ancora fallirebbe
        da_travasare = "ruolo" in _colonne(con, "utenti")
        if da_travasare:
            con.execute("ALTER TABLE utenti RENAME TO utenti_vecchia")

        con.executescript(SCHEMA)
        _semina_ruoli(con)
        _colonne_mancanti(con)
        if da_travasare:
            _copia_utenti(con)
        con.execute("PRAGMA foreign_keys=ON")


# -------------------------------------------------------------------- password
def cifra_password(password: str) -> str:
    """Impronta autodescrittiva: ``algoritmo$parametri$sale$impronta``.

    Salvare i parametri accanto all'impronta e' cio' che permette di alzare il
    costo in futuro senza azzerare le password di tutti.
    """
    sale = secrets.token_bytes(16)
    impronta = hashlib.pbkdf2_hmac("sha256", password.encode(), sale, ITERAZIONI_PBKDF2)
    return f"pbkdf2_sha256${ITERAZIONI_PBKDF2}${sale.hex()}${impronta.hex()}"


def verifica_password(password: str, salvata: str) -> tuple[bool, bool]:
    """Ritorna ``(valida, da_ricalcolare)``."""
    try:
        algoritmo, parametri, sale_hex, atteso = salvata.split("$")
    except ValueError:
        return False, False
    if algoritmo != "pbkdf2_sha256":
        return False, False

    iterazioni = int(parametri)
    calcolata = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(sale_hex), iterazioni
    ).hex()
    # confronto a tempo costante: un confronto normale rivela, dal tempo di
    # risposta, quanti caratteri iniziali coincidono
    valida = hmac.compare_digest(calcolata, atteso)
    return valida, valida and iterazioni < ITERAZIONI_PBKDF2


def valuta_politica_password(password: str, utente: str = "", email: str = "") -> str:
    """Ritorna il motivo del rifiuto, stringa vuota se la password va bene.

    Lunghezza minima e liste di password diffuse, come da NIST SP 800-63B.
    Nessun obbligo di maiuscole o simboli: produce ``Password1!`` e nient'altro.
    """
    if len(password) < 12:
        return "la password deve avere almeno 12 caratteri"
    if len(password) > 128:
        return "la password non puo' superare i 128 caratteri"
    minuscola = password.lower()
    for pezzo in (utente, email.split("@")[0] if email else ""):
        if pezzo and len(pezzo) >= 4 and pezzo.lower() in minuscola:
            return "la password non puo' contenere il nome utente o l'indirizzo"
    if minuscola in _PASSWORD_DIFFUSE:
        return "questa password e' fra le piu' diffuse: scegline un'altra"
    return ""


_PASSWORD_DIFFUSE = frozenset(
    {
        "password1234",
        "passwordpassword",
        "123456789012",
        "qwertyuiopas",
        "amministratore",
        "comuneimperia",
        "navetteimperia",
    }
)


# ---------------------------------------------------------------------- utenti
def _ruolo_id(con, codice: str) -> int:
    riga = con.execute("SELECT id FROM ruoli WHERE codice=?", (codice,)).fetchone()
    if not riga:
        raise ValueError(f"ruolo sconosciuto: {codice}")
    return riga["id"]


_SELEZIONE = (
    "SELECT u.*, r.codice AS ruolo, r.nome AS ruolo_nome "
    "FROM utenti u JOIN ruoli r ON r.id = u.ruolo_id "
)


def crea_utente(
    utente: str,
    email: str,
    nome: str = "",
    ruolo: str = "tecnico",
    password: str = "",
    creato_da: Optional[int] = None,
) -> int:
    """Crea un'utenza. Senza password l'accesso avviene tramite token di invito."""
    with connessione() as con:
        try:
            cur = con.execute(
                "INSERT INTO utenti (utente, email, nome, ruolo_id, hash_password, "
                "creato_il, creato_da) VALUES (?,?,?,?,?,?,?)",
                (
                    utente.strip(),
                    email.strip().lower(),
                    nome.strip(),
                    _ruolo_id(con, ruolo),
                    cifra_password(password) if password else "",
                    _adesso(),
                    creato_da,
                ),
            )
        except sqlite3.IntegrityError as e:
            raise UtenteDuplicato("nome utente o indirizzo gia' assegnati") from e
        return cur.lastrowid


def leggi_utente(utente: str) -> Optional[Dict[str, Any]]:
    with connessione() as con:
        r = con.execute(_SELEZIONE + "WHERE u.utente = ?", (utente,)).fetchone()
        return dict(r) if r else None


def leggi_utente_per_id(utente_id: int) -> Optional[Dict[str, Any]]:
    with connessione() as con:
        r = con.execute(_SELEZIONE + "WHERE u.id = ?", (utente_id,)).fetchone()
        return dict(r) if r else None


def leggi_per_identificativo(identificativo: str) -> Optional[Dict[str, Any]]:
    """Cerca per nome utente OPPURE per indirizzo email.

    Chi riceve l'invito ha in mano il proprio indirizzo, ed e' quello che
    digita: rifiutarlo produce un "credenziali non valide" incomprensibile,
    perche' la password e' giusta davvero. Entrambi i campi sono unici, quindi
    non c'e' ambiguita'.
    """
    ident = identificativo.strip()
    with connessione() as con:
        r = con.execute(
            _SELEZIONE + "WHERE u.utente = ? OR u.email = ?", (ident, ident.lower())
        ).fetchone()
        return dict(r) if r else None


def leggi_utente_per_email(email: str) -> Optional[Dict[str, Any]]:
    with connessione() as con:
        r = con.execute(
            _SELEZIONE + "WHERE u.email = ?", (email.strip().lower(),)
        ).fetchone()
        return dict(r) if r else None


def esistono_utenti() -> bool:
    with connessione() as con:
        return bool(
            con.execute("SELECT 1 FROM utenti WHERE stato='attivo' LIMIT 1").fetchone()
        )


def elenco_utenti(includi_archiviati: bool = False) -> List[Dict[str, Any]]:
    dove = "" if includi_archiviati else "WHERE u.stato <> 'archiviato' "
    with connessione() as con:
        return [dict(r) for r in con.execute(_SELEZIONE + dove + "ORDER BY u.utente")]


def verifica_credenziali(utente: str, password: str) -> Dict[str, Any]:
    """Ritorna ``{"utente": riga|None, "motivo": str}``.

    Il motivo serve al registro, non all'utente: a video il messaggio deve
    restare identico in ogni caso di fallimento.
    """
    riga = leggi_per_identificativo(utente)
    if not riga:
        # calcolo a vuoto: il tempo di risposta non deve distinguere
        # "utenza inesistente" da "password sbagliata"
        cifra_password(password)
        return {"utente": None, "motivo": "utenza inesistente"}

    if riga["bloccato_fino"] and riga["bloccato_fino"] > _adesso():
        return {"utente": None, "motivo": "bloccato per troppi tentativi"}

    if not riga["hash_password"]:
        cifra_password(password)
        return {"utente": None, "motivo": "primo accesso non ancora effettuato"}

    valida, da_ricalcolare = verifica_password(password, riga["hash_password"])
    if not valida:
        _conta_fallimento(riga["id"])
        return {"utente": None, "motivo": "password errata"}

    if riga["stato"] != "attivo":
        return {"utente": None, "motivo": f"utenza {riga['stato']}"}

    with connessione() as con:
        if da_ricalcolare:
            # unico momento in cui la password in chiaro e' disponibile:
            # se ne approfitta per adeguare l'impronta ai parametri correnti
            con.execute(
                "UPDATE utenti SET hash_password=? WHERE id=?",
                (cifra_password(password), riga["id"]),
            )
        con.execute(
            "UPDATE utenti SET ultimo_accesso=?, tentativi_falliti=0, "
            "bloccato_fino=NULL WHERE id=?",
            (_adesso(), riga["id"]),
        )
    return {"utente": leggi_utente_per_id(riga["id"]), "motivo": ""}


def _conta_fallimento(utente_id: int) -> None:
    with connessione() as con:
        con.execute(
            "UPDATE utenti SET tentativi_falliti = tentativi_falliti + 1 WHERE id=?",
            (utente_id,),
        )
        riga = con.execute(
            "SELECT tentativi_falliti FROM utenti WHERE id=?", (utente_id,)
        ).fetchone()
        if riga and riga["tentativi_falliti"] >= TENTATIVI_MASSIMI:
            con.execute(
                "UPDATE utenti SET bloccato_fino=? WHERE id=?",
                (_fra(minutes=BLOCCO_MINUTI), utente_id),
            )


def sblocca(utente_id: int) -> None:
    with connessione() as con:
        con.execute(
            "UPDATE utenti SET tentativi_falliti=0, bloccato_fino=NULL WHERE id=?",
            (utente_id,),
        )


def aggiorna_anagrafica(utente_id: int, nome: str, email: str) -> None:
    with connessione() as con:
        try:
            con.execute(
                "UPDATE utenti SET nome=?, email=?, modificato_il=? WHERE id=?",
                (nome.strip(), email.strip().lower(), _adesso(), utente_id),
            )
        except sqlite3.IntegrityError as e:
            raise UtenteDuplicato("indirizzo gia' assegnato a un'altra utenza") from e


def cambia_stato_o_ruolo(
    utente_id: int,
    nuovo_stato: Optional[str] = None,
    nuovo_ruolo: Optional[str] = None,
) -> None:
    """Cambia stato e/o ruolo impedendo di restare senza amministratori.

    Il conteggio va fatto DENTRO la transazione: verificarlo prima lascia una
    finestra in cui due declassamenti concorrenti passano entrambi il controllo
    e il sistema si riapre solo dal database.
    """
    if nuovo_stato and nuovo_stato not in STATI:
        raise ValueError(f"stato sconosciuto: {nuovo_stato}")

    with connessione() as con:
        con.execute("BEGIN IMMEDIATE")
        riga = con.execute(_SELEZIONE + "WHERE u.id = ?", (utente_id,)).fetchone()
        if not riga:
            raise ValueError("utenza inesistente")

        era_amministratore = (
            riga["ruolo"] == AMMINISTRATORE and riga["stato"] == "attivo"
        )
        perde = (nuovo_stato and nuovo_stato != "attivo") or (
            nuovo_ruolo and nuovo_ruolo != AMMINISTRATORE
        )
        if era_amministratore and perde:
            rimasti = con.execute(
                "SELECT COUNT(*) FROM utenti u JOIN ruoli r ON r.id=u.ruolo_id "
                "WHERE r.codice=? AND u.stato='attivo' AND u.id<>?",
                (AMMINISTRATORE, utente_id),
            ).fetchone()[0]
            if rimasti == 0:
                raise UltimoAmministratore(
                    "questo e' l'ultimo amministratore attivo: nominane un altro "
                    "prima di procedere"
                )

        campi, valori = [], []
        if nuovo_stato:
            campi.append("stato=?")
            valori.append(nuovo_stato)
        if nuovo_ruolo:
            campi.append("ruolo_id=?")
            valori.append(_ruolo_id(con, nuovo_ruolo))
        if not campi:
            return
        # ogni cambio di stato o di ruolo deve avere effetto SUBITO: senza
        # incrementare l'epoca, l'utente continua con i vecchi permessi finche'
        # la sua sessione non scade
        campi += ["epoca_sessione = epoca_sessione + 1", "modificato_il=?"]
        valori += [_adesso(), utente_id]
        con.execute(f"UPDATE utenti SET {', '.join(campi)} WHERE id=?", valori)


def imposta_password(utente_id: int, password: str) -> None:
    """Salva la nuova password e chiude tutto quello che restava aperto."""
    with connessione() as con:
        con.execute(
            "UPDATE utenti SET hash_password=?, deve_cambiare_password=0, "
            "tentativi_falliti=0, bloccato_fino=NULL, "
            "epoca_sessione = epoca_sessione + 1, modificato_il=? WHERE id=?",
            (cifra_password(password), _adesso(), utente_id),
        )
        # gli altri inviti o richieste di reset in circolazione non servono piu'
        con.execute(
            "UPDATE token_accesso SET invalidato_il=? WHERE utente_id=? "
            "AND usato_il IS NULL AND invalidato_il IS NULL",
            (_adesso(), utente_id),
        )


# ----------------------------------------------------------------------- token
def impronta_token(token: str) -> str:
    """Il token ha gia' 256 bit di entropia: non e' attaccabile a dizionario,
    quindi SHA-256 semplice basta e non serve una funzione lenta."""
    return hashlib.sha256(token.encode()).hexdigest()


def crea_token(
    utente_id: int, tipo: str = "reset", ip: str = "", agente: str = ""
) -> str:
    """Genera il token, ne salva SOLO l'impronta e restituisce il valore in chiaro.

    Il chiamante lo usa per comporre il link e poi lo dimentica: da questo
    momento nessuno, database compreso, e' in grado di ricostruirlo.
    """
    token = secrets.token_urlsafe(32)
    scadenza = (
        _fra(hours=DURATA_TOKEN_PRIMO_ACCESSO_ORE)
        if tipo == "primo_accesso"
        else _fra(minutes=DURATA_TOKEN_RESET_MIN)
    )
    with connessione() as con:
        con.execute(
            "INSERT INTO token_accesso (utente_id, tipo, hash_token, creato_il, "
            "scade_il, ip_richiesta, agente) VALUES (?,?,?,?,?,?,?)",
            (
                utente_id,
                tipo,
                impronta_token(token),
                _adesso(),
                scadenza,
                ip,
                agente[:300],
            ),
        )
    return token


def verifica_token(token: str) -> Optional[Dict[str, Any]]:
    """Controlla il token senza consumarlo: serve alla GET del modulo.

    Consumarlo qui lo brucerebbe quando un antivirus o un client di posta
    precarica il link prima che l'utente veda la pagina.
    """
    with connessione() as con:
        r = con.execute(
            "SELECT * FROM token_accesso WHERE hash_token=?", (impronta_token(token),)
        ).fetchone()
    if not r or r["usato_il"] or r["invalidato_il"] or r["scade_il"] < _adesso():
        return None
    return dict(r)


def consuma_token(token: str) -> Optional[Dict[str, Any]]:
    """Verifica e consuma in modo atomico. Ritorna l'utente, o None."""
    with connessione() as con:
        con.execute("BEGIN IMMEDIATE")
        r = con.execute(
            "SELECT * FROM token_accesso WHERE hash_token=?", (impronta_token(token),)
        ).fetchone()
        if not r or r["usato_il"] or r["invalidato_il"] or r["scade_il"] < _adesso():
            return None
        # UPDATE condizionato: se due richieste arrivano insieme, solo una
        # trova ancora usato_il nullo
        cur = con.execute(
            "UPDATE token_accesso SET usato_il=? WHERE id=? AND usato_il IS NULL",
            (_adesso(), r["id"]),
        )
        if cur.rowcount != 1:
            return None
        utente = con.execute(
            _SELEZIONE + "WHERE u.id = ?", (r["utente_id"],)
        ).fetchone()
    return dict(utente) if utente else None


def pulisci_token(giorni: int = 7) -> int:
    """Rimuove i token scaduti da oltre N giorni.

    Non si cancellano subito alla scadenza: dopo un incidente serve poter
    rispondere a "quante richieste sono partite da quell'indirizzo".
    """
    limite = (datetime.now(timezone.utc) - timedelta(days=giorni)).isoformat(
        timespec="seconds"
    )
    with connessione() as con:
        cur = con.execute("DELETE FROM token_accesso WHERE scade_il < ?", (limite,))
        return cur.rowcount


# ----------------------------------------------------- registro e lavorazioni
def registra(
    azione: str,
    utente: str = "",
    esito: str = "ok",
    dettaglio: str = "",
    indirizzo_ip: str = "",
    bersaglio: str = "",
) -> None:
    """Annota un'azione. Non solleva mai: il log non deve impedire l'operazione
    che stava tracciando."""
    try:
        with connessione() as con:
            con.execute(
                "INSERT INTO registro (quando, utente, bersaglio, azione, esito, "
                "dettaglio, indirizzo_ip) VALUES (?,?,?,?,?,?,?)",
                (
                    _adesso(),
                    utente,
                    bersaglio,
                    azione,
                    esito,
                    dettaglio[:2000],
                    indirizzo_ip,
                ),
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


def mesi_registro(escluso_corrente: bool = True) -> List[str]:
    """Mesi presenti nel registro, dal piu' vecchio, come ``AAAA-MM``.

    Il mese in corso si esclude: archiviarlo darebbe una copia incompleta che
    andrebbe poi riscritta, e una copia riscritta non e' piu' una prova.
    """
    corrente = datetime.now(timezone.utc).strftime("%Y-%m")
    with connessione() as con:
        mesi = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT substr(quando, 1, 7) FROM registro ORDER BY 1"
            )
        ]
    return [m for m in mesi if not (escluso_corrente and m == corrente)]


def leggi_registro_mese(mese: str) -> List[Dict[str, Any]]:
    """Tutte le voci di un mese ``AAAA-MM``, in ordine cronologico."""
    with connessione() as con:
        return [
            dict(r)
            for r in con.execute(
                "SELECT * FROM registro WHERE substr(quando, 1, 7) = ? ORDER BY id",
                (mese,),
            )
        ]


def elimina_registro_mese(mese: str) -> int:
    """Rimuove le voci di un mese. Il chiamante deve aver gia' verificato che
    la copia archiviata esista: senza, questa e' una perdita di dati."""
    with connessione() as con:
        cur = con.execute(
            "DELETE FROM registro WHERE substr(quando, 1, 7) = ?", (mese,)
        )
        return cur.rowcount


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
