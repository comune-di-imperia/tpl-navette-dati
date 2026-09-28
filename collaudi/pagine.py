"""Apre TUTTE le pagine con una sessione vera e controlla che rispondano.

Fermarsi al 302 verso l'accesso non collauda niente: la pagina puo' rompersi
un istante dopo, ed e' quello che e' successo con /email, andata in produzione
con un errore che il solo controllo del reindirizzamento non poteva vedere.

Le pagine non si elencano a mano — un elenco scritto a mano dimentica proprio
quella nuova — ma si prendono dalla mappa degli indirizzi dell'applicazione:
tutte quelle raggiungibili in lettura e senza parametri.

La sessione si costruisce a partire da un amministratore vero letto dal
database, perche' la guardia rilegge l'utente a ogni richiesta e una sessione
inventata non passa. Non crea ne' modifica utenze.
"""

import sys

from scripts.tpl_navette import db
from scripts.tpl_navette.app import app

# Indirizzi che non vanno interrogati a vuoto. Uscire chiuderebbe la sessione;
# lo scarico dell'archivio vuole sapere quale archivio, e senza risponde 400 —
# che e' la risposta giusta, non un guasto.
ESCLUSI = {"/uscita", "/static/<path:filename>", "/archivio/scarica"}


def amministratore() -> dict:
    for riga in db.elenco_utenti():
        if riga["ruolo"] == "amministratore" and riga["stato"] == "attivo":
            return riga
    raise SystemExit("nessun amministratore attivo: collaudo impossibile")


def pagine() -> list:
    trovate = []
    for regola in app.url_map.iter_rules():
        if "GET" not in (regola.methods or set()):
            continue
        if regola.arguments:  # richiede parametri: non si indovina
            continue
        percorso = str(regola.rule)
        if percorso in ESCLUSI:
            continue
        trovate.append(percorso)
    return sorted(set(trovate))


capo = amministratore()
app.config["TESTING"] = True
guasti = []

with app.test_client() as cliente:
    with cliente.session_transaction() as sessione:
        sessione["utente_id"] = capo["id"]
        sessione["utente"] = capo["utente"]
        sessione["epoca"] = capo["epoca_sessione"]

    for percorso in pagine():
        try:
            risposta = cliente.get(percorso, follow_redirects=False)
        except Exception as errore:  # noqa: BLE001
            guasti.append(f"{percorso}: eccezione {type(errore).__name__}: {errore}")
            print(f"  GUASTO {percorso:<34} eccezione")
            continue
        codice = risposta.status_code
        print(f"  {'ok    ' if codice < 400 else 'GUASTO'} {percorso:<34} {codice}")
        if codice >= 400:
            guasti.append(f"{percorso}: {codice}")

print()
if guasti:
    print("PAGINE CHE NON RISPONDONO:")
    for g in guasti:
        print("  ", g)
    sys.exit(1)
print("tutte le pagine rispondono")
