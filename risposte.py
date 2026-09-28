"""Risposte standard ai messaggi dei cittadini.

Stanno qui e non nella pagina perche' il testo di una risposta data a nome
dell'Amministrazione non e' una scelta di chi in quel momento ha il mouse in
mano: si decide una volta, si scrive una volta, e chi risponde lo rilegge prima
di premere invio. Resta modificabile a video, perche' nessun modello copre
tutti i casi.
"""

from __future__ import annotations

from typing import Dict, List

FIRMA = """
Sperimentazione trasporto pubblico locale a guida autonoma
Comune di Imperia
tplauto@comune.imperia.it
"""

CANCELLAZIONE_AUTONOMA = """Gentile utente,

può cancellare il suo account, e con esso i suoi dati personali, direttamente
dall'applicazione che utilizza per il servizio: vi acceda e, in alto a destra,
troverà l'opzione per cancellare l'account.

Se non fosse in grado di procedere in autonomia, ci scriva nuovamente
precisando che non riesce a cancellare l'account: provvederemo noi.
"""

CANCELLAZIONE_ESEGUITA = """Gentile utente,

abbiamo dato corso alla sua richiesta: i suoi dati personali sono stati
cancellati e la sua registrazione al servizio non esiste più.

Le restano solo, in forma anonima, i dati statistici dei viaggi già effettuati:
non contengono nome, cognome, indirizzo di posta elettronica né alcun altro
elemento che permetta di risalire a lei, e per questo non rientrano nella
richiesta di cancellazione.
"""

CANCELLAZIONE_NON_TROVATA = """Gentile utente,

abbiamo cercato la sua registrazione a partire dall'indirizzo di posta
elettronica da cui ci ha scritto, ma non risulta alcun account collegato a
questo indirizzo.

Se si era registrata con un indirizzo diverso, ce lo indichi e procederemo. Se
invece non risulta registrata, non abbiamo alcun suo dato personale da
cancellare.
"""

GENERICA = """Gentile utente,

grazie per averci scritto.

"""

# La prima e' quella che si apre gia' selezionata: e' il caso che si presenta
# piu' spesso, ed e' anche quello che si risolve senza far aspettare nessuno.
MODELLI: List[Dict[str, str]] = [
    {
        "chiave": "cancellazione_autonoma",
        "titolo": "Cancellazione dati — può farlo da solo",
        "quando": "Risposta predefinita a chi chiede la cancellazione dei dati",
        "testo": CANCELLAZIONE_AUTONOMA,
    },
    {
        "chiave": "cancellazione_eseguita",
        "titolo": "Cancellazione dati — eseguita da noi",
        "quando": "Dopo aver cancellato l'account dalla pagina",
        "testo": CANCELLAZIONE_ESEGUITA,
    },
    {
        "chiave": "cancellazione_non_trovata",
        "titolo": "Cancellazione dati — nessun account con quell'indirizzo",
        "quando": "Quando la ricerca per indirizzo non trova nulla",
        "testo": CANCELLAZIONE_NON_TROVATA,
    },
    {
        "chiave": "generica",
        "titolo": "Risposta libera",
        "quando": "Per tutto il resto",
        "testo": GENERICA,
    },
]

PREDEFINITO = MODELLI[0]["chiave"]


def modello(chiave: str) -> Dict[str, str]:
    for m in MODELLI:
        if m["chiave"] == chiave:
            return m
    return MODELLI[0]


def con_firma(testo: str) -> str:
    """Il testo con la firma in coda, senza raddoppiarla se c'e' gia'."""
    testo = (testo or "").rstrip()
    if "Comune di Imperia" in testo:
        return testo + "\n"
    return f"{testo}\n{FIRMA}"
