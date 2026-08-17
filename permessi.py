"""Ruoli e permessi dell'applicazione.

La matrice sta nel codice e non in una tabella: i ruoli derivano
dall'organigramma del committente, sono noti e non vengono creati dagli
utenti. Metterli in database significherebbe versionare a mano una
configurazione che nessuno modifica, e togliere ai test la possibilita' di
verificarla.

Se un domani i permessi dovranno essere modificabili da interfaccia, cambia
l'implementazione di :func:`puo`, non i suoi chiamanti.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

AMMINISTRATORE = "amministratore"
TECNICO = "tecnico"
CONSULTAZIONE = "consultazione"

# (codice, etichetta, descrizione) - l'ordine e' quello dei menu a tendina
RUOLI: Tuple[Tuple[str, str, str], ...] = (
    (
        AMMINISTRATORE,
        "Amministratore",
        "Gestisce l'utenza e ha accesso a tutte le funzioni",
    ),
    (
        TECNICO,
        "Tecnico",
        "Carica gli archivi, consulta elaborazioni e report",
    ),
    (
        CONSULTAZIONE,
        "Consultazione",
        "Consulta elaborazioni, archivio e report senza poter agire",
    ),
)

ETICHETTE: Dict[str, str] = {codice: nome for codice, nome, _ in RUOLI}

# Permessi in uso. Un permesso in piu' qui e un decoratore sulla rotta sono
# tutto quello che serve per introdurre una nuova funzione protetta.
GESTIONE_UTENTI = "utenti.gestione"
CARICA_DATI = "dati.carica"
LEGGE_DATI = "dati.leggi"
SCARICA_REPORT = "report.scarica"
# Distinto da SCARICA_REPORT: il report e' una sintesi, i file dell'archivio
# sono il dato grezzo di sperimentazione. Consultazione legge le sintesi ma non
# porta via gli originali.
SCARICA_ARCHIVIO = "archivio.scarica"
LEGGE_REGISTRO = "registro.leggi"

PERMESSI: Dict[str, frozenset] = {
    AMMINISTRATORE: frozenset(
        {
            GESTIONE_UTENTI,
            CARICA_DATI,
            LEGGE_DATI,
            SCARICA_REPORT,
            SCARICA_ARCHIVIO,
            LEGGE_REGISTRO,
        }
    ),
    TECNICO: frozenset({CARICA_DATI, LEGGE_DATI, SCARICA_REPORT, SCARICA_ARCHIVIO}),
    CONSULTAZIONE: frozenset({LEGGE_DATI, SCARICA_REPORT}),
}


def puo(ruolo: str, permesso: str) -> bool:
    """Vero se il ruolo comprende il permesso."""
    return permesso in PERMESSI.get(ruolo, frozenset())


def elenco_permessi(ruolo: str) -> List[str]:
    """Permessi del ruolo, ordinati. Serve al frontend per disegnare i menu."""
    return sorted(PERMESSI.get(ruolo, frozenset()))
