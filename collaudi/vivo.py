"""Interroga il servizio IN ESECUZIONE, non un processo nuovo.

Un collaudo che importa l'applicazione da capo prova che il codice sul disco
e' buono; non prova che il servizio lo stia usando. Fra i due c'e' un riavvio
che si puo' dimenticare — ed e' quello che e' successo.

Si firma un cookie di sessione con la stessa chiave del servizio e si bussa
alla porta da fuori: se risponde 200, e' il processo vivo ad averlo fatto.
"""

import urllib.request

from flask.sessions import SecureCookieSessionInterface

from scripts.tpl_navette import db
from scripts.tpl_navette.app import app

PAGINE = ["/email", "/casella", "/statistiche", "/registro"]

capo = next(r for r in db.elenco_utenti()
            if r["ruolo"] == "amministratore" and r["stato"] == "attivo")

serializzatore = SecureCookieSessionInterface().get_signing_serializer(app)
cookie = serializzatore.dumps({
    "utente_id": capo["id"],
    "utente": capo["utente"],
    "epoca": capo["epoca_sessione"],
})

guasti = []
for pagina in PAGINE:
    richiesta = urllib.request.Request(
        f"http://127.0.0.1:8080{pagina}",
        headers={"Cookie": f"session={cookie}", "Host": "tpl.comune.imperia.it"},
    )
    try:
        with urllib.request.urlopen(richiesta, timeout=30) as risposta:
            codice, corpo = risposta.status, risposta.read()
    except urllib.error.HTTPError as errore:
        codice, corpo = errore.code, errore.read()
    except Exception as errore:  # noqa: BLE001
        guasti.append(f"{pagina}: {type(errore).__name__} {errore}")
        print(f"  GUASTO {pagina:<14} {type(errore).__name__}")
        continue

    print(f"  {'ok    ' if codice < 400 else 'GUASTO'} {pagina:<14} {codice} "
          f"({len(corpo)} byte)")
    if codice >= 400:
        guasti.append(f"{pagina}: {codice}")

print()
print("il servizio in esecuzione serve tutte le pagine" if not guasti
      else f"ANCORA GUASTE: {guasti}")
