"""Applicazione web per il caricamento e l'analisi dei dati delle navette TPL.

Comune di Imperia - sperimentazione navette a guida autonoma.

Gli archivi giornalieri pesano 70-90 MB e l'analisi richiede decine di secondi:
l'elaborazione avviene quindi in una coda servita da un thread, mentre la pagina
mostra l'avanzamento interrogando ``/stato/<id>``. Per questo il servizio va
avviato con **un solo processo** (piu' thread), altrimenti la coda si sdoppia.
"""

from __future__ import annotations

import io
import json
import logging
import os
import queue
import re
import secrets
import threading
import zipfile
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from . import analisi, db, pipeline

logger = logging.getLogger("tpl.app")

DIMENSIONE_MASSIMA = int(os.environ.get("TPL_MAX_MB", "512")) * 1024 * 1024
ESTENSIONI = {".zip"}
RE_NOME_ATTESO = re.compile(
    r"^[A-Za-z0-9]+_\d{8}_[A-Za-z0-9]+_\d{8}_\d{2}h\d{2}m\d{2}s"
)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("TPL_SECRET_KEY") or secrets.token_hex(32),
    MAX_CONTENT_LENGTH=DIMENSIONE_MASSIMA,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("TPL_COOKIE_SICURO", "1") == "1",
    PERMANENT_SESSION_LIFETIME=8 * 3600,
)

_coda: queue.Queue[int] = queue.Queue()
# percorso del file caricato, in attesa che il thread di lavorazione lo prenda
_percorsi: dict[int, Path] = {}


# --------------------------------------------------------------------- accesso
def accesso_libero() -> bool:
    """Vero finche' non esiste nessun operatore registrato.

    In questa fase l'applicazione e' raggiungibile solo da indirizzi autorizzati
    dal firewall del VPS, quindi la lista utenti sarebbe un ostacolo senza
    guadagno. Appena si crea il primo operatore con ``cli utente-crea`` le
    credenziali tornano obbligatorie da sole, senza cambiare configurazione.
    ``TPL_RICHIEDI_ACCESSO=1`` forza comunque l'autenticazione.
    """
    if os.environ.get("TPL_RICHIEDI_ACCESSO", "0") == "1":
        return False
    return not db.esistono_utenti()


def _utente() -> str:
    """Chi sta operando: il nome se autenticato, altrimenti l'indirizzo IP.

    Senza credenziali il registro non puo' dire "chi", ma deve comunque dire
    "da dove": e' l'unica identita' disponibile e va scritta come tale.
    """
    return session.get("utente") or f"IP {_ip()}"


def richiede_accesso(f):
    @wraps(f)
    def guardia(*a, **kw):
        if "utente_id" not in session and not accesso_libero():
            return redirect(url_for("accesso", prossima=request.path))
        return f(*a, **kw)

    return guardia


def _ip() -> str:
    """IP del client: dietro il reverse proxy Apache vale l'ultimo hop noto."""
    inoltrato = request.headers.get("X-Forwarded-For", "")
    return (inoltrato.split(",")[0].strip() if inoltrato else request.remote_addr) or ""


def _gettone() -> str:
    """Gettone anti-CSRF, uno per sessione."""
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _verifica_gettone() -> None:
    inviato = request.form.get("csrf") or request.headers.get("X-CSRF-Token", "")
    if not inviato or not secrets.compare_digest(inviato, session.get("csrf", "")):
        abort(400, "richiesta non valida")


app.jinja_env.globals["gettone"] = _gettone
app.jinja_env.globals["accesso_libero"] = accesso_libero


@app.route("/accesso", methods=["GET", "POST"])
def accesso():
    if accesso_libero():
        return redirect(url_for("cruscotto"))
    if request.method == "POST":
        utente = (request.form.get("utente") or "").strip()
        password = request.form.get("password") or ""
        riga = db.verifica_credenziali(utente, password)
        if not riga:
            db.registra("accesso", utente=utente, esito="rifiutato", indirizzo_ip=_ip())
            flash("Credenziali non valide.", "errore")
            return render_template("accesso.html"), 401

        session.clear()
        session.permanent = True
        session["utente_id"] = riga["id"]
        session["utente"] = riga["utente"]
        session["ruolo"] = riga["ruolo"]
        db.registra("accesso", utente=riga["utente"], indirizzo_ip=_ip())

        prossima = request.args.get("prossima", "")
        # solo percorsi interni: evita di essere usati come trampolino
        return redirect(prossima if prossima.startswith("/") else url_for("cruscotto"))
    return render_template("accesso.html")


@app.route("/uscita")
def uscita():
    if "utente" in session:
        db.registra("uscita", utente=session["utente"], indirizzo_ip=_ip())
    session.clear()
    return redirect(url_for("accesso"))


# -------------------------------------------------------------------- cruscotto
@app.route("/")
@richiede_accesso
def cruscotto():
    return render_template(
        "cruscotto.html", elaborazioni=db.elenco_elaborazioni(limite=25)
    )


@app.route("/carica", methods=["POST"])
@richiede_accesso
def carica():
    _verifica_gettone()
    caricato = request.files.get("archivio")
    if not caricato or not caricato.filename:
        return jsonify(errore="nessun file ricevuto"), 400

    nome = secure_filename(caricato.filename)

    def _rifiuta(motivo: str):
        db.registra(
            "caricamento",
            utente=_utente(),
            esito="rifiutato",
            dettaglio=f"{nome}: {motivo}",
            indirizzo_ip=_ip(),
        )
        return jsonify(errore=motivo), 400

    if Path(nome).suffix.lower() not in ESTENSIONI:
        return _rifiuta("sono ammessi solo archivi .zip")
    if not RE_NOME_ATTESO.match(nome):
        return _rifiuta(
            "nome non conforme: atteso "
            "<navetta>_<data>_<VIN>_<data>_<ora>.zip come prodotto dall'estrattore"
        )

    pipeline.UPLOAD.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d%H%M%S")
    destinazione = pipeline.UPLOAD / f"{marca}_{nome}"
    caricato.save(destinazione)
    dimensione = destinazione.stat().st_size

    # Controllo d'integrita' prima di accodare: costa meno di un secondo e
    # risparmia all'operatore un'elaborazione che fallirebbe comunque. Un
    # archivio rovinato alla fonte va rimandato indietro subito, non archiviato.
    try:
        integrita = analisi.verifica_integrita(destinazione)
    except analisi.ArchivioNonValido as e:
        destinazione.unlink(missing_ok=True)
        db.registra(
            "caricamento",
            utente=_utente(),
            esito="rifiutato",
            dettaglio=f"{nome}: {e}",
            indirizzo_ip=_ip(),
        )
        return jsonify(errore=str(e)), 400

    rovinati = {pc: motivo for pc, motivo in integrita.items() if motivo}
    if len(rovinati) == len(integrita):
        destinazione.unlink(missing_ok=True)
        db.registra(
            "caricamento",
            utente=_utente(),
            esito="rifiutato",
            dettaglio=f"{nome}: tutti i computer di bordo danneggiati",
            indirizzo_ip=_ip(),
        )
        return (
            jsonify(
                errore="archivio inutilizzabile: nessun computer di bordo leggibile. "
                "Il file e' danneggiato all'origine, va richiesto di nuovo."
            ),
            400,
        )

    avviso = (
        "dati di "
        + ", ".join(sorted(rovinati))
        + " danneggiati all'origine: l'elaborazione prosegue solo sul resto"
        if rovinati
        else ""
    )

    elab_id = db.apri_elaborazione(session.get("utente_id"), nome, dimensione)
    db.registra(
        "caricamento",
        utente=_utente(),
        dettaglio=f"{nome} ({dimensione / 1024 / 1024:.1f} MB) -> elaborazione {elab_id}",
        indirizzo_ip=_ip(),
    )
    _coda.put(elab_id)
    _percorsi[elab_id] = destinazione
    return jsonify(id=elab_id, nome=nome, dimensione=dimensione, avviso=avviso), 202


@app.route("/stato/<int:elab_id>")
@richiede_accesso
def stato(elab_id: int):
    riga = db.leggi_elaborazione(elab_id)
    if not riga:
        abort(404)
    return jsonify(
        id=riga["id"],
        stato=riga["stato"],
        messaggio=riga["messaggio"],
        navetta=riga["navetta"],
        data_dati=riga["data_dati"],
        chiave_s3=riga["chiave_s3"],
        in_coda=_coda.qsize(),
    )


@app.route("/elaborazione/<int:elab_id>")
@richiede_accesso
def dettaglio(elab_id: int):
    riga = db.leggi_elaborazione(elab_id)
    if not riga:
        abort(404)
    riga = dict(riga)
    riga["riepilogo"] = json.loads(riga.get("riepilogo") or "{}")
    return render_template("elaborazione.html", e=riga)


@app.route("/report")
@richiede_accesso
def report():
    voci = db.elenco_elaborazioni(limite=1000, solo_con_report=True)
    for v in voci:
        v["disponibile"] = (pipeline.OUTPUT / v["pdf"]).exists()
    return render_template("report.html", voci=voci)


def _percorso_report(nome: str) -> Path:
    """Percorso del PDF, garantito dentro la cartella di uscita.

    Il nome arriva dal database, non dall'utente, ma il controllo resta: e' la
    differenza fra un bug e una lettura arbitraria del filesystem.
    """
    percorso = (pipeline.OUTPUT / nome).resolve()
    if percorso.parent != pipeline.OUTPUT.resolve() or not percorso.is_file():
        abort(404)
    return percorso


@app.route("/report/<int:elab_id>")
@richiede_accesso
def scarica_report(elab_id: int):
    riga = db.leggi_elaborazione(elab_id)
    if not riga or not riga["pdf"]:
        abort(404)
    db.registra(
        "scarico report",
        utente=_utente(),
        dettaglio=riga["pdf"],
        indirizzo_ip=_ip(),
    )
    return send_file(_percorso_report(riga["pdf"]), as_attachment=True)


@app.route("/report/tutti.zip")
@richiede_accesso
def scarica_tutti_i_report():
    voci = db.elenco_elaborazioni(limite=1000, solo_con_report=True)
    memoria = io.BytesIO()
    inclusi = 0
    with zipfile.ZipFile(memoria, "w", zipfile.ZIP_DEFLATED) as z:
        for v in voci:
            percorso = pipeline.OUTPUT / v["pdf"]
            if not percorso.is_file():
                continue
            # nome ordinabile: navetta e giornata dei dati, non l'id interno
            etichetta = (
                f"{v['navetta'] or 'sconosciuta'}_{v['data_dati'] or 'senza-data'}"
            )
            z.write(percorso, f"{etichetta}_{v['pdf']}")
            inclusi += 1
    if not inclusi:
        abort(404)
    memoria.seek(0)
    db.registra(
        "scarico report",
        utente=_utente(),
        dettaglio=f"archivio completo, {inclusi} report",
        indirizzo_ip=_ip(),
    )
    return send_file(
        memoria,
        mimetype="application/zip",
        as_attachment=True,
        download_name="report-navette.zip",
    )


@app.route("/registro")
@richiede_accesso
def registro():
    filtro = request.args.get("utente", "")
    return render_template(
        "registro.html",
        voci=db.leggi_registro(limite=500, utente=filtro),
        filtro=filtro,
        utenti=db.elenco_utenti(),
    )


# ------------------------------------------------------------------- lavorazione
def _lavora(elab_id: int) -> None:
    percorso = _percorsi.pop(elab_id, None)
    riga = db.leggi_elaborazione(elab_id)
    utente = (riga or {}).get("utente", "")
    if not percorso or not percorso.exists():
        db.chiudi_elaborazione(elab_id, "fallita", "file caricato non piu' disponibile")
        return

    try:
        esito = pipeline.elabora(percorso)
    except Exception as e:  # l'errore va mostrato all'operatore, non perso nei log
        logger.exception("elaborazione fallita", extra={"elaborazione": elab_id})
        db.chiudi_elaborazione(elab_id, "fallita", f"{type(e).__name__}: {e}")
        db.registra(
            "elaborazione",
            utente=utente,
            esito="fallita",
            dettaglio=f"{percorso.name}: {e}",
        )
        return

    db.chiudi_elaborazione(
        elab_id,
        "completata",
        messaggio="; ".join(
            f"{a['contesto']}: {a['messaggio']}" for a in esito.get("anomalie", [])
        ),
        riepilogo={
            pc: info.get("riepilogo", {})
            for pc, info in (esito.get("computer") or {}).items()
        },
        chiave_s3=esito.get("chiave_s3", ""),
        navetta=esito.get("navetta", ""),
        vin=esito.get("vin", ""),
        data_dati=esito.get("data_file", ""),
        pdf=esito.get("pdf", ""),
    )
    db.registra(
        "elaborazione",
        utente=utente,
        dettaglio=f"{percorso.name} -> {esito.get('chiave_s3', '')}",
    )
    percorso.unlink(missing_ok=True)


def _servi_coda() -> None:
    while True:
        elab_id = _coda.get()
        try:
            _lavora(elab_id)
        finally:
            _coda.task_done()


def avvia() -> Flask:
    db.inizializza()
    threading.Thread(target=_servi_coda, name="tpl-lavorazione", daemon=True).start()
    return app


avvia()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("TPL_PORTA", "8080")))
