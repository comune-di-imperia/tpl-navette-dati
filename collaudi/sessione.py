"""Collaudo: sessione scaduta segnalata come tale, non come errore di proxy."""

import os
import sys
import tempfile
from pathlib import Path

cartella = tempfile.mkdtemp(prefix="tplsess-")
os.environ["TPL_DB"] = os.path.join(cartella, "tpl.sqlite")
os.environ["TPL_DATI"] = cartella
os.environ["TPL_SECRET_KEY"] = "collaudo"
os.environ["TPL_COOKIE_SICURO"] = "0"

# la radice del repository, tre livelli sopra questo file
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.tpl_navette import app as modulo
from scripts.tpl_navette import db

app = modulo.app
app.config["TESTING"] = True
db.inizializza()
db.crea_utente("capo", "capo@esempio.it", "Capo", "amministratore", "frase-lunga-prova")

esiti = []


def verifica(nome, condizione, extra=""):
    esiti.append((nome, bool(condizione)))
    print(
        ("  OK  " if condizione else " FALLITO ")
        + nome
        + (f"  {extra}" if extra else "")
    )


XHR = {"X-Requested-With": "XMLHttpRequest"}

print("\n[ senza sessione ]")
c = app.test_client()

r = c.get("/")
verifica(
    "la pagina viene rimandata all'accesso",
    r.status_code == 302 and "/accesso" in r.headers.get("Location", ""),
)

r = c.post("/carica", headers=XHR)
verifica(
    "il caricamento asincrono riceve 401, non un rimando",
    r.status_code == 401,
    f"({r.status_code})",
)
corpo = r.get_json()
verifica(
    "la risposta e' JSON e dice che la sessione e' scaduta",
    corpo and corpo.get("scaduta") is True,
    str(corpo),
)
verifica(
    "il messaggio e' comprensibile",
    corpo and "sessione scaduta" in corpo["errore"],
    str(corpo),
)

r = c.get("/api/io", headers=XHR)
verifica("il controllo preventivo risponde 401", r.status_code == 401)

r = c.get("/archivio", headers=XHR)
verifica("anche le altre rotte asincrone danno 401", r.status_code == 401)
r = c.get("/archivio")
verifica("ma il browser normale continua a essere rimandato", r.status_code == 302)

print("\n[ con sessione valida ]")
c2 = app.test_client()
c2.post("/accesso", data={"utente": "capo", "password": "frase-lunga-prova"})
r = c2.get("/api/io", headers=XHR)
verifica("il controllo preventivo passa", r.status_code == 200)
verifica("riporta l'utenza", r.get_json()["utente"] == "capo")

r = c2.get("/")
verifica("la pagina invia l'intestazione XMLHttpRequest", b"X-Requested-With" in r.data)
verifica("la pagina controlla prima di spedire", b"/api/io" in r.data)
verifica("c'e' il messaggio di sessione scaduta", b"sessione scaduta" in r.data)

falliti = [n for n, ok in esiti if not ok]
print(f"\n{len(esiti) - len(falliti)}/{len(esiti)} superati")
if falliti:
    print("falliti: " + "; ".join(falliti))
sys.exit(1 if falliti else 0)
