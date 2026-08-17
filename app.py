"""Applicazione web per il caricamento e l'analisi dei dati delle navette TPL.

Comune di Imperia - sperimentazione navette a guida autonoma.

Gli archivi giornalieri pesano 70-90 MB e l'analisi richiede decine di secondi:
l'elaborazione avviene quindi in una coda servita da un thread, mentre la pagina
mostra l'avanzamento interrogando ``/stato/<id>``. Per questo il servizio va
avviato con **un solo processo** (piu' thread), altrimenti la coda si sdoppia.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import queue
import re
import secrets
import shutil
import threading
import time
import zipfile
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Optional

from flask import (
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    session,
    stream_with_context,
    url_for,
)
from werkzeug.utils import secure_filename

from . import analisi, db, esplora, manuale, permessi, pipeline, posta

logger = logging.getLogger("tpl.app")

DIMENSIONE_MASSIMA = int(os.environ.get("TPL_MAX_MB", "512")) * 1024 * 1024

# Lo ZIP raccoglie i due computer di bordo, ma l'estrattore Navya consegna un
# tar.gz per computer: si accettano entrambe le forme.
ESTENSIONI = (".zip", ".tar.gz", ".tgz")
RE_NOME_ATTESO = re.compile(
    r"^[A-Za-z0-9]+_\d{8}_[A-Za-z0-9]+_\d{8}_\d{2}h\d{2}m\d{2}s"
)


def _estensione_ammessa(nome: str) -> bool:
    return nome.lower().endswith(ESTENSIONI)


def _base(nome: str) -> str:
    """Prefisso che identifica navetta e giornata, senza il ``_pcN`` finale.

    Serve a capire se piu' file caricati insieme sono i computer della stessa
    giornata o roba scollegata.
    """
    trovato = RE_NOME_ATTESO.match(nome)
    return trovato.group(0) if trovato else nome


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
    """Vero finche' non esiste nessuna utenza attiva.

    In questa fase l'applicazione e' raggiungibile solo da indirizzi autorizzati
    dal firewall del VPS, quindi la lista utenti sarebbe un ostacolo senza
    guadagno. Appena si crea il primo utente le credenziali tornano obbligatorie
    da sole, senza cambiare configurazione; ``TPL_RICHIEDI_ACCESSO=1`` le impone
    comunque.
    """
    if os.environ.get("TPL_RICHIEDI_ACCESSO", "0") == "1":
        return False
    return not db.esistono_utenti()


def utente_corrente() -> Optional[dict]:
    """Utente della sessione, rileggendolo dal database a ogni richiesta.

    E' questa rilettura a rendere IMMEDIATI sospensione e cambio di ruolo:
    fidandosi di quanto scritto nel cookie all'accesso, un utente sospeso
    continuerebbe a lavorare finche' la sessione non scade.
    """
    uid = session.get("utente_id")
    if not uid:
        return None
    riga = db.leggi_utente_per_id(uid)
    if (
        not riga
        or riga["stato"] != "attivo"
        or riga["epoca_sessione"] != session.get("epoca")
    ):
        session.clear()
        return None
    return riga


def _utente() -> str:
    """Chi sta operando: il nome se autenticato, altrimenti l'indirizzo IP.

    Senza credenziali il registro non puo' dire "chi", ma deve comunque dire
    "da dove": e' l'unica identita' disponibile e va scritta come tale.
    """
    return session.get("utente") or f"IP {_ip()}"


def _ha_permesso(permesso: str) -> bool:
    if accesso_libero():
        return True  # nessuna utenza: la protezione e' il filtro sugli IP
    riga = utente_corrente()
    return bool(riga and permessi.puo(riga["ruolo"], permesso))


def richiede_permesso(permesso: str):
    """Protegge la rotta. Va messo su TUTTE, comprese quelle che tornano JSON:
    nascondere una voce di menu non impedisce di richiamare l'indirizzo."""

    def decoratore(f):
        @wraps(f)
        def guardia(*a, **kw):
            if accesso_libero():
                return f(*a, **kw)
            riga = utente_corrente()
            if not riga:
                return redirect(url_for("accesso", prossima=request.path))
            if not permessi.puo(riga["ruolo"], permesso):
                db.registra(
                    "accesso negato",
                    utente=riga["utente"],
                    esito="rifiutato",
                    dettaglio=permesso,
                    indirizzo_ip=_ip(),
                )
                abort(403)
            return f(*a, **kw)

        return guardia

    return decoratore


# retrocompatibilita' interna: le rotte di sola lettura chiedono il permesso
# minimo di consultazione
def richiede_accesso(f):
    return richiede_permesso(permessi.LEGGE_DATI)(f)


# ------------------------------------------------------- limitazione richieste
# Due contatori distinti: per utenza contro la forza bruta mirata, per indirizzo
# contro il credential stuffing su molte utenze. In memoria perche' il servizio
# gira volutamente in un solo processo (vedi intestazione del modulo).
_tentativi: dict[str, list[float]] = {}


def limite_consentito(chiave: str, massimo: int, finestra_s: int) -> bool:
    adesso = time.monotonic()
    eventi = [t for t in _tentativi.get(chiave, []) if adesso - t < finestra_s]
    eventi.append(adesso)
    _tentativi[chiave] = eventi
    return len(eventi) <= massimo


def _ip() -> str:
    """Indirizzo del client, dietro il reverse proxy Apache.

    Conta l'ULTIMO valore di ``X-Forwarded-For``, non il primo: Apache accoda in
    fondo l'indirizzo che ha visto davvero, mentre tutto quello che precede lo
    ha scritto il client e puo' essere inventato di sana pianta. Prendendo il
    primo si lascerebbe falsificare il registro e aggirare il blocco per
    tentativi ripetuti semplicemente inviando un'intestazione a piacere.
    """
    inoltrato = request.headers.get("X-Forwarded-For", "")
    if inoltrato:
        return inoltrato.split(",")[-1].strip()
    return request.remote_addr or ""


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
# il template NASCONDE cio' che l'utente non puo' fare; a impedirlo davvero e'
# il decoratore sulla rotta, non questo
app.jinja_env.globals["ha_permesso"] = _ha_permesso


@app.route("/accesso", methods=["GET", "POST"])
def accesso():
    if accesso_libero():
        return redirect(url_for("cruscotto"))
    if request.method != "POST":
        return render_template("accesso.html")

    utente = (request.form.get("utente") or "").strip()
    password = request.form.get("password") or ""

    if not limite_consentito(f"accesso-ip:{_ip()}", massimo=20, finestra_s=3600):
        db.registra(
            "accesso",
            utente=utente,
            esito="rifiutato",
            dettaglio="troppi tentativi dall'indirizzo",
            indirizzo_ip=_ip(),
        )
        return render_template("accesso.html", errore=_ERRORE_ACCESSO), 429

    esito = db.verifica_credenziali(utente, password)
    riga = esito["utente"]
    if not riga:
        # il motivo va nel registro, MAI a video: distinguere "utenza
        # inesistente" da "password errata" regala l'elenco degli iscritti
        db.registra(
            "accesso",
            utente=utente or "(vuoto)",
            esito="rifiutato",
            dettaglio=esito["motivo"],
            indirizzo_ip=_ip(),
        )
        return render_template("accesso.html", errore=_ERRORE_ACCESSO), 401

    # azzeramento prima di scrivere: impedisce la session fixation, in cui
    # l'attaccante impone alla vittima una sessione che conosce gia'
    session.clear()
    session.permanent = True
    session["utente_id"] = riga["id"]
    session["utente"] = riga["utente"]
    session["epoca"] = riga["epoca_sessione"]
    db.registra("accesso", utente=riga["utente"], indirizzo_ip=_ip())

    if riga["deve_cambiare_password"]:
        return redirect(url_for("cambia_password"))
    prossima = request.args.get("prossima", "")
    # solo percorsi interni: evita di essere usati come trampolino
    return redirect(prossima if prossima.startswith("/") else url_for("cruscotto"))


_ERRORE_ACCESSO = "Credenziali non valide."


@app.route("/uscita")
def uscita():
    if "utente" in session:
        db.registra("uscita", utente=session["utente"], indirizzo_ip=_ip())
    session.clear()
    return redirect(url_for("accesso"))


@app.route("/password/cambia", methods=["GET", "POST"])
def cambia_password():
    riga = utente_corrente()
    if not riga:
        return redirect(url_for("accesso"))
    if request.method != "POST":
        return render_template("cambia_password.html")

    _verifica_gettone()
    attuale = request.form.get("attuale") or ""
    nuova = request.form.get("password") or ""

    valida, _ = db.verifica_password(attuale, riga["hash_password"])
    if not valida:
        return (
            render_template(
                "cambia_password.html", errore="La password attuale non e' corretta."
            ),
            400,
        )

    motivo = db.valuta_politica_password(nuova, riga["utente"], riga["email"])
    if motivo:
        return render_template("cambia_password.html", errore=motivo.capitalize()), 400

    db.imposta_password(riga["id"], nuova)
    db.registra("password cambiata", utente=riga["utente"], indirizzo_ip=_ip())
    _avvisa(riga)
    # imposta_password ha incrementato l'epoca: la sessione corrente e' scaduta
    session.clear()
    flash("Password aggiornata: accedi con quella nuova.", "esito")
    return redirect(url_for("accesso"))


def _avvisa(riga: dict) -> None:
    """Notifica di cambio password. Un guasto SMTP non deve far fallire il
    cambio, che a quel punto e' gia' avvenuto: si annota e si prosegue."""
    try:
        posta.avvisa_cambio_password(riga["email"], riga["nome"])
    except Exception as e:
        logger.warning("avviso di cambio password non inviato", exc_info=e)


# -------------------------------------------------------------------- cruscotto
@app.route("/")
@richiede_accesso
def cruscotto():
    return render_template(
        "cruscotto.html", elaborazioni=db.elenco_elaborazioni(limite=25)
    )


@app.route("/carica", methods=["POST"])
@richiede_permesso(permessi.CARICA_DATI)
def carica():
    _verifica_gettone()
    caricati = [f for f in request.files.getlist("archivio") if f and f.filename]
    if not caricati:
        return jsonify(errore="nessun file ricevuto"), 400

    nomi = [secure_filename(f.filename) for f in caricati]
    etichetta = nomi[0] if len(nomi) == 1 else f"{len(nomi)} file di {_base(nomi[0])}"

    def _rifiuta(motivo: str):
        db.registra(
            "caricamento",
            utente=_utente(),
            esito="rifiutato",
            dettaglio=f"{etichetta}: {motivo}",
            indirizzo_ip=_ip(),
        )
        return jsonify(errore=motivo), 400

    for nome in nomi:
        if not _estensione_ammessa(nome):
            return _rifiuta(
                f"{nome}: sono ammessi archivi .zip oppure i .tar.gz "
                "prodotti dall'estrattore"
            )
        if not RE_NOME_ATTESO.match(nome):
            return _rifiuta(
                f"{nome}: nome non conforme, atteso "
                "<navetta>_<data>_<VIN>_<data>_<ora> come prodotto dall'estrattore"
            )
    # piu' file insieme hanno senso solo se sono i computer della STESSA giornata
    if len({_base(n) for n in nomi}) > 1:
        return _rifiuta(
            "i file caricati insieme appartengono a giornate o navette diverse: "
            "caricali separatamente"
        )

    # I file vanno tenuti col LORO nome: navetta, VIN e giornata si leggono da
    # li'. Per questo l'unicita' si ottiene con una sottocartella, non con un
    # prefisso al nome (che romperebbe il riconoscimento dei metadati).
    cartella = pipeline.UPLOAD / datetime.now().strftime("%Y%m%d%H%M%S%f")
    cartella.mkdir(parents=True, exist_ok=True)
    for f, nome in zip(caricati, nomi):
        f.save(cartella / nome)

    # un solo ZIP resta un file; i tar.gz sciolti si elaborano come insieme
    destinazione = (
        cartella / nomi[0]
        if len(nomi) == 1 and not analisi.e_tar(nomi[0])
        else cartella
    )
    dimensione = sum((cartella / n).stat().st_size for n in nomi)

    # Stesso contenuto gia' elaborato? Si riconosce dall'impronta, non dal nome:
    # un file rinominato resta lo stesso file. Il ricarico si puo' comunque
    # forzare, perche' a volte serve rifare l'elaborazione con codice nuovo.
    impronta = db.impronta_file([cartella / n for n in nomi])
    if request.form.get("forza") != "1":
        gia = db.elaborazione_con_impronta(impronta)
        if gia:
            shutil.rmtree(cartella, ignore_errors=True)
            quando = (gia["conclusa_il"] or gia["iniziata_il"])[:16].replace("T", " ")
            db.registra(
                "caricamento",
                utente=_utente(),
                esito="rifiutato",
                dettaglio=f"{etichetta}: gia' elaborato ({gia['id']})",
                indirizzo_ip=_ip(),
            )
            return (
                jsonify(
                    errore=f"questo contenuto e' gia' stato elaborato il {quando} "
                    f"come «{gia['nome_file']}»",
                    duplicato=gia["id"],
                ),
                409,
            )

    # Controllo d'integrita' prima di accodare: costa meno di un secondo e
    # risparmia all'operatore un'elaborazione che fallirebbe comunque. Un
    # archivio rovinato alla fonte va rimandato indietro subito, non archiviato.
    try:
        integrita = analisi.verifica_integrita(destinazione)
    except analisi.ArchivioNonValido as e:
        shutil.rmtree(cartella, ignore_errors=True)
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
        shutil.rmtree(cartella, ignore_errors=True)
        db.registra(
            "caricamento",
            utente=_utente(),
            esito="rifiutato",
            dettaglio=f"{nome}: tutti i computer di bordo danneggiati",
            indirizzo_ip=_ip(),
        )
        # cifrato e danneggiato richiedono due rimedi diversi: chiedere la
        # password a chi produce i file, oppure farsi rimandare l'archivio
        protetto = all("password" in m for m in rovinati.values())
        return (
            jsonify(
                errore=(
                    "archivio protetto da password: la chiave non e' configurata "
                    "sul server. Va richiesta a chi produce i file e impostata "
                    "in TPL_ZIP_PASSWORD."
                    if protetto
                    else "archivio inutilizzabile: nessun computer di bordo "
                    "leggibile. Il file e' danneggiato all'origine, va richiesto "
                    "di nuovo."
                )
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

    elab_id = db.apri_elaborazione(
        session.get("utente_id"),
        etichetta,
        dimensione,
        operatore=_utente(),
        impronta=impronta,
    )
    db.registra(
        "caricamento",
        utente=_utente(),
        dettaglio=f"{etichetta} ({dimensione / 1024 / 1024:.1f} MB) -> elaborazione {elab_id}",
        indirizzo_ip=_ip(),
    )
    _coda.put(elab_id)
    _percorsi[elab_id] = destinazione
    return (
        jsonify(id=elab_id, nome=etichetta, dimensione=dimensione, avviso=avviso),
        202,
    )


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
@richiede_permesso(permessi.SCARICA_REPORT)
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
@richiede_permesso(permessi.SCARICA_REPORT)
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
@richiede_permesso(permessi.SCARICA_REPORT)
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


# ----------------------------------------------------------------- manuale
@app.route("/manuale/<lingua>.pdf")
def scarica_manuale(lingua: str):
    """Manuale d'uso. Pubblico: serve anche a chi non riesce ad accedere."""
    if lingua not in manuale.LINGUE:
        abort(404)
    percorso = pipeline.OUTPUT / f"manuale-{lingua}.pdf"
    # si compone una volta sola e si tiene: cambia solo quando cambia il codice
    if not percorso.exists():
        manuale.genera(lingua, percorso)
    return send_file(
        percorso,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"manuale-navette-{lingua}.pdf",
    )


# ------------------------------------------------------------ archivio su S3
def _consenti_prefisso(chiave: str) -> None:
    """Alcuni rami dell'archivio non sono per tutti.

    Il controllo va rifatto sulla CHIAVE al momento dello scarico, non solo
    sull'elenco: nascondere una cartella non impedisce di indovinarne il nome.
    """
    permesso = esplora.permesso_richiesto(chiave)
    if permesso and not _ha_permesso(permesso):
        riga = utente_corrente()
        db.registra(
            "accesso negato",
            utente=_utente(),
            esito="rifiutato",
            dettaglio=f"archivio {chiave}",
            indirizzo_ip=_ip(),
        )
        del riga
        abort(403)


@app.route("/archivio")
@richiede_permesso(permessi.LEGGE_DATI)
def archivio():
    prefisso = request.args.get("p", "")
    _consenti_prefisso(prefisso)
    try:
        contenuto = esplora.elenca(prefisso, request.args.get("segue", ""))
    except Exception as e:
        logger.error("elenco dell'archivio non riuscito", exc_info=e)
        return render_template("archivio.html", errore=str(e), contenuto=None), 502

    # i rami riservati spariscono dall'elenco per chi non puo' vederli
    contenuto["cartelle"] = [
        c
        for c in contenuto["cartelle"]
        if not esplora.permesso_richiesto(c["prefisso"])
        or _ha_permesso(esplora.permesso_richiesto(c["prefisso"]))
    ]
    return render_template("archivio.html", contenuto=contenuto, errore=None)


@app.route("/archivio/scarica")
@richiede_permesso(permessi.SCARICA_ARCHIVIO)
def scarica_da_archivio():
    chiave = request.args.get("chiave", "")
    if not chiave or chiave.endswith("/"):
        abort(400)
    _consenti_prefisso(chiave)

    voce = esplora.dettagli(chiave)
    if not voce:
        abort(404)

    db.registra(
        "scarico da archivio",
        utente=_utente(),
        dettaglio=f"{chiave} ({voce['dimensione'] / 1024 / 1024:.1f} MB)",
        indirizzo_ip=_ip(),
    )
    risposta = Response(
        stream_with_context(esplora.flusso(chiave)),
        mimetype="application/octet-stream",
    )
    risposta.headers["Content-Length"] = str(voce["dimensione"])
    risposta.headers["Content-Disposition"] = (
        f'attachment; filename="{chiave.rsplit("/", 1)[-1]}"'
    )
    return risposta


@app.route("/registro/esporta.csv")
@richiede_permesso(permessi.LEGGE_REGISTRO)
def esporta_registro():
    filtro = request.args.get("utente", "")
    voci = db.leggi_registro(limite=100_000, utente=filtro)

    testo = io.StringIO()
    # punto e virgola e BOM: e' quello che Excel in configurazione italiana
    # apre in colonne senza chiedere nulla
    scrittore = csv.writer(testo, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    scrittore.writerow(
        [
            "Data e ora (UTC)",
            "Operatore",
            "Bersaglio",
            "Azione",
            "Esito",
            "Dettaglio",
            "Indirizzo IP",
        ]
    )
    for v in voci:
        scrittore.writerow(
            [
                v["quando"],
                v["utente"],
                v["bersaglio"],
                v["azione"],
                v["esito"],
                v["dettaglio"],
                v["indirizzo_ip"],
            ]
        )

    db.registra(
        "esportazione registro",
        utente=_utente(),
        dettaglio=f"{len(voci)} voci" + (f", filtro {filtro}" if filtro else ""),
        indirizzo_ip=_ip(),
    )
    oggi = datetime.now().strftime("%Y%m%d")
    return send_file(
        io.BytesIO(testo.getvalue().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"registro-navette-{oggi}.csv",
    )


@app.route("/registro")
@richiede_permesso(permessi.LEGGE_REGISTRO)
def registro():
    filtro = request.args.get("utente", "")
    return render_template(
        "registro.html",
        voci=db.leggi_registro(limite=500, utente=filtro),
        filtro=filtro,
        utenti=db.elenco_utenti(),
    )


# ------------------------------------------------------ recupero password
@app.route("/password/richiedi", methods=["GET", "POST"])
def richiedi_password():
    if request.method != "POST":
        return render_template("password_richiedi.html")
    _verifica_gettone()

    email = (request.form.get("email") or "").strip().lower()
    if not limite_consentito(f"reset-ip:{_ip()}", massimo=5, finestra_s=3600):
        return (
            render_template(
                "password_richiedi.html", errore="Troppe richieste: riprova fra un'ora."
            ),
            429,
        )

    riga = db.leggi_utente_per_email(email)
    # solo un'utenza attiva riceve il link; sospesa o archiviata, nessun invio
    if riga and riga["stato"] == "attivo":
        token = db.crea_token(
            riga["id"],
            "reset",
            ip=_ip(),
            agente=request.headers.get("User-Agent", ""),
        )
        try:
            posta.invia_link_password(
                riga["email"], token, "reset", riga["nome"], riga["utente"]
            )
        except Exception as e:
            logger.error("invio del link di recupero fallito", exc_info=e)

    db.registra(
        "richiesta recupero password", utente=email or "(vuoto)", indirizzo_ip=_ip()
    )

    # RISPOSTA IDENTICA in ogni caso - esistente, inesistente, sospeso:
    # altrimenti chiunque puo' scoprire quali indirizzi sono registrati
    return render_template("password_inviata.html")


@app.route("/password/reimposta/<token>", methods=["GET", "POST"])
def reimposta_password(token: str):
    if request.method != "POST":
        # la GET verifica ma NON consuma: il precaricamento del link fatto da
        # certi client di posta o antivirus brucerebbe il token prima che
        # l'utente veda il modulo
        voce = db.verifica_token(token)
        if not voce:
            return render_template("password_reimposta.html", scaduto=True), 400
        risposta = make_response(
            render_template(
                "password_reimposta.html",
                token=token,
                primo_accesso=voce["tipo"] == "primo_accesso",
            )
        )
        # il token e' nell'indirizzo: senza questo finirebbe nell'intestazione
        # Referer verso qualunque risorsa esterna
        risposta.headers["Referrer-Policy"] = "no-referrer"
        return risposta

    _verifica_gettone()
    nuova = request.form.get("password") or ""

    voce = db.verifica_token(token)
    if not voce:
        return render_template("password_reimposta.html", scaduto=True), 400

    utente = db.leggi_utente_per_id(voce["utente_id"])
    motivo = db.valuta_politica_password(nuova, utente["utente"], utente["email"])
    if motivo:
        # politica prima del consumo: un rifiuto per password debole non deve
        # bruciare il token e costringere a rifare tutta la richiesta
        return (
            render_template(
                "password_reimposta.html", token=token, errore=motivo.capitalize()
            ),
            400,
        )

    utente = db.consuma_token(token)
    if not utente:
        return render_template("password_reimposta.html", scaduto=True), 400

    db.imposta_password(utente["id"], nuova)
    db.registra("password reimpostata", utente=utente["utente"], indirizzo_ip=_ip())
    _avvisa(utente)
    flash("Password impostata: ora puoi accedere.", "esito")
    return redirect(url_for("accesso"))


# ------------------------------------------------------ area amministratori
@app.route("/amministrazione/utenti")
@richiede_permesso(permessi.GESTIONE_UTENTI)
def utenti():
    return render_template(
        "utenti.html",
        utenti=db.elenco_utenti(
            includi_archiviati=request.args.get("archiviati") == "1"
        ),
        ruoli=permessi.RUOLI,
        archiviati=request.args.get("archiviati") == "1",
    )


@app.route("/amministrazione/utenti/crea", methods=["POST"])
@richiede_permesso(permessi.GESTIONE_UTENTI)
def crea_utente():
    _verifica_gettone()
    nome_utente = (request.form.get("utente") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    ruolo = request.form.get("ruolo") or permessi.TECNICO

    if not nome_utente or not email:
        flash("Nome utente e indirizzo email sono obbligatori.", "errore")
        return redirect(url_for("utenti"))
    if ruolo not in permessi.ETICHETTE:
        abort(400)

    io_stesso = utente_corrente()
    try:
        uid = db.crea_utente(
            nome_utente,
            email,
            request.form.get("nome", ""),
            ruolo,
            creato_da=io_stesso["id"] if io_stesso else None,
        )
    except db.UtenteDuplicato as e:
        flash(str(e).capitalize() + ".", "errore")
        return redirect(url_for("utenti"))

    db.registra(
        "creazione utenza",
        utente=_utente(),
        bersaglio=nome_utente,
        dettaglio=f"ruolo {ruolo}",
        indirizzo_ip=_ip(),
    )
    # nessuna password scelta da altri: si invia un invito e la sceglie il
    # titolare, cosi' non esiste il momento in cui due persone la conoscono
    _invia_invito(uid, nome_utente)
    return redirect(url_for("utenti"))


def _invia_invito(utente_id: int, nome_utente: str) -> None:
    riga = db.leggi_utente_per_id(utente_id)
    token = db.crea_token(utente_id, "primo_accesso", ip=_ip())
    try:
        posta.invia_link_password(
            riga["email"], token, "primo_accesso", riga["nome"], riga["utente"]
        )
        flash(
            f"Utenza {nome_utente} creata: invito inviato a {riga['email']}.", "esito"
        )
    except Exception as e:
        logger.error("invio dell'invito fallito", exc_info=e)
        flash(
            f"Utenza {nome_utente} creata, ma l'invito NON e' partito "
            "(posta non configurata o irraggiungibile): usa 'reinvia invito'.",
            "errore",
        )


@app.route("/amministrazione/utenti/<int:utente_id>", methods=["POST"])
@richiede_permesso(permessi.GESTIONE_UTENTI)
def modifica_utente(utente_id: int):
    _verifica_gettone()
    bersaglio = db.leggi_utente_per_id(utente_id)
    if not bersaglio:
        abort(404)
    io_stesso = utente_corrente()
    azione = request.form.get("azione", "")

    try:
        if azione == "anagrafica":
            db.aggiorna_anagrafica(
                utente_id, request.form.get("nome", ""), request.form.get("email", "")
            )
            _annota("modifica anagrafica", bersaglio)

        elif azione == "ruolo":
            nuovo = request.form.get("ruolo", "")
            if nuovo not in permessi.ETICHETTE:
                abort(400)
            # un amministratore non cambia il PROPRIO ruolo: toglie una classe
            # intera di autoesclusioni e non costa nulla, c'e' sempre un collega
            if io_stesso and io_stesso["id"] == utente_id:
                flash("Non puoi cambiare il tuo ruolo: chiedi a un collega.", "errore")
                return redirect(url_for("utenti"))
            db.cambia_stato_o_ruolo(utente_id, nuovo_ruolo=nuovo)
            _annota("modifica ruolo", bersaglio, f"{bersaglio['ruolo']} -> {nuovo}")

        elif azione in ("sospendi", "riattiva", "archivia"):
            stato = {
                "sospendi": "sospeso",
                "riattiva": "attivo",
                "archivia": "archiviato",
            }[azione]
            db.cambia_stato_o_ruolo(utente_id, nuovo_stato=stato)
            _annota(f"utenza {stato}", bersaglio)

        elif azione == "sblocca":
            db.sblocca(utente_id)
            _annota("sblocco utenza", bersaglio)

        elif azione == "reimposta":
            token = db.crea_token(utente_id, "reset", ip=_ip())
            posta.invia_link_password(
                bersaglio["email"],
                token,
                "reset",
                bersaglio["nome"],
                bersaglio["utente"],
            )
            _annota("invio link di recupero", bersaglio)
            flash(f"Link inviato a {bersaglio['email']}.", "esito")

        elif azione == "invito":
            _invia_invito(utente_id, bersaglio["utente"])
        else:
            abort(400)

    except db.UltimoAmministratore as e:
        db.registra(
            "modifica utenza",
            utente=_utente(),
            bersaglio=bersaglio["utente"],
            esito="rifiutato",
            dettaglio=str(e),
            indirizzo_ip=_ip(),
        )
        flash(str(e).capitalize() + ".", "errore")
    except db.UtenteDuplicato as e:
        flash(str(e).capitalize() + ".", "errore")
    except posta.PostaNonConfigurata as e:
        flash(f"Posta non configurata: {e}", "errore")

    return redirect(url_for("utenti"))


def _annota(azione: str, bersaglio: dict, dettaglio: str = "") -> None:
    db.registra(
        azione,
        utente=_utente(),
        bersaglio=bersaglio["utente"],
        dettaglio=dettaglio,
        indirizzo_ip=_ip(),
    )


@app.route("/api/io")
def io_stesso():
    """Permessi dell'utente corrente: il frontend ci costruisce i menu.

    Rispecchia le decisioni del backend, non le prende: ogni rotta verifica
    comunque per conto proprio.
    """
    if accesso_libero():
        return jsonify(
            utente=None,
            accesso_libero=True,
            permessi=sorted(permessi.PERMESSI[permessi.AMMINISTRATORE]),
        )
    riga = utente_corrente()
    if not riga:
        return jsonify(errore="non autenticato"), 401
    return jsonify(
        utente=riga["utente"],
        nome=riga["nome"],
        ruolo=riga["ruolo"],
        ruolo_nome=riga["ruolo_nome"],
        accesso_libero=False,
        permessi=permessi.elenco_permessi(riga["ruolo"]),
    )


# ------------------------------------------------------------------- lavorazione
def _lavora(elab_id: int) -> None:
    percorso = _percorsi.pop(elab_id, None)
    riga = db.leggi_elaborazione(elab_id)
    utente = (riga or {}).get("operatore") or (riga or {}).get("utente", "")
    # con i tar.gz sciolti il percorso e' la cartella temporanea, che ha un nome
    # tecnico: nel registro deve comparire quello che ha caricato l'operatore
    etichetta = (riga or {}).get("nome_file") or (percorso.name if percorso else "?")
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
            dettaglio=f"{etichetta}: {e}",
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
        dettaglio=f"{etichetta} -> {esito.get('chiave_s3', '')}",
    )
    shutil.rmtree(
        percorso if percorso.is_dir() else percorso.parent, ignore_errors=True
    )


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
