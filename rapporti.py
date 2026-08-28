"""Destinatari dei rapporti periodici sulle statistiche.

Chi riceve il riepilogo settimanale e quello mensile cambia nel tempo, e deve
poterlo cambiare chi amministra il servizio senza mettere mano alla
configurazione del server. L'elenco sta quindi in un file, non in una
variabile d'ambiente.

Ogni destinatario dichiara **quali** rapporti vuole: c'e' chi segue la
sperimentazione settimana per settimana e chi ha bisogno del solo consuntivo
mensile. Mandare tutto a tutti e' il modo piu' sicuro per farsi ignorare.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

# I due rapporti previsti. Il valore e' quello che si scrive nel file.
CADENZE = {
    "settimanale": "Ogni lunedi', sulla settimana conclusa",
    "mensile": "Il primo del mese, sul mese concluso",
}


def percorso_destinatari() -> Path:
    return Path(os.environ.get(
        "TPL_RAPPORTI_DESTINATARI",
        "/var/lib/tpl-navette/destinatari-rapporti.json"))


def _vuoti() -> Dict[str, Any]:
    return {"email": []}


def leggi_destinatari() -> Dict[str, Any]:
    """Elenco configurato. File assente o illeggibile: elenco vuoto.

    Un rapporto che non parte e' un fastidio; un'applicazione che non si apre
    perche' manca un file di configurazione e' un guasto.
    """
    percorso = percorso_destinatari()
    if not percorso.exists():
        return _vuoti()
    try:
        dati = json.loads(percorso.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, PermissionError):
        return _vuoti()

    voci = []
    for voce in dati.get("email", []):
        if not voce.get("indirizzo"):
            continue
        cadenze = [c for c in voce.get("cadenze", []) if c in CADENZE]
        voci.append({
            "indirizzo": voce["indirizzo"],
            "nota": voce.get("nota", ""),
            "cadenze": cadenze or ["settimanale"],
        })
    return {"email": voci}


def _scrivi(dati: Dict[str, Any]) -> None:
    percorso = percorso_destinatari()
    percorso.parent.mkdir(parents=True, exist_ok=True)
    tmp = percorso.with_suffix(".tmp")
    tmp.write_text(json.dumps(dati, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(percorso)
    # Lo scrive l'applicazione, lo rilegge il programma che invia i rapporti:
    # senza lettura al gruppo il secondo non troverebbe nessuno a cui scrivere.
    try:
        percorso.chmod(0o664)
    except OSError:
        pass


def _pulisci(valore: str) -> str:
    return (valore or "").strip().lower()


def indirizzo_valido(valore: str) -> bool:
    """Controllo volutamente permissivo: qui l'errore tipico e' la distrazione,
    non l'indirizzo esotico. La verifica vera la fa il server di posta."""
    valore = _pulisci(valore)
    if valore.count("@") != 1 or " " in valore:
        return False
    locale, dominio = valore.split("@")
    return bool(locale) and "." in dominio and not dominio.startswith(".")


def _cadenze_valide(cadenze: List[str]) -> List[str]:
    scelte = [c for c in cadenze if c in CADENZE]
    if not scelte:
        raise ValueError("Scegli almeno un rapporto da inviare.")
    return scelte


def aggiungi(indirizzo: str, cadenze: List[str], nota: str = "") -> str:
    indirizzo = _pulisci(indirizzo)
    if not indirizzo_valido(indirizzo):
        raise ValueError("Indirizzo non valido.")
    scelte = _cadenze_valide(cadenze)

    dati = leggi_destinatari()
    if any(v["indirizzo"] == indirizzo for v in dati["email"]):
        raise ValueError("Questo indirizzo e' gia' fra i destinatari.")

    dati["email"].append({
        "indirizzo": indirizzo,
        "cadenze": scelte,
        "nota": nota.strip()[:120],
    })
    _scrivi(dati)
    return f"Aggiunto {indirizzo}."


def modifica(indirizzo: str, cadenze: List[str], nota: str = "") -> str:
    indirizzo = _pulisci(indirizzo)
    scelte = _cadenze_valide(cadenze)

    dati = leggi_destinatari()
    for voce in dati["email"]:
        if voce["indirizzo"] == indirizzo:
            voce["cadenze"] = scelte
            voce["nota"] = nota.strip()[:120]
            _scrivi(dati)
            return f"Aggiornato {indirizzo}."
    raise ValueError("Destinatario non trovato.")


def elimina(indirizzo: str) -> str:
    """Toglie un destinatario. Qui si puo' arrivare a zero.

    A differenza degli avvisi della casella, che avvertono di un cittadino in
    attesa di risposta, un rapporto periodico che non parte non lascia nessuno
    appeso: se non lo vuole piu' nessuno, si smette di mandarlo.
    """
    indirizzo = _pulisci(indirizzo)
    dati = leggi_destinatari()
    restanti = [v for v in dati["email"] if v["indirizzo"] != indirizzo]
    if len(restanti) == len(dati["email"]):
        raise ValueError("Destinatario non trovato.")
    dati["email"] = restanti
    _scrivi(dati)
    return f"Rimosso {indirizzo}."


def per_cadenza(cadenza: str) -> List[str]:
    """Indirizzi che hanno chiesto quel rapporto."""
    return [
        v["indirizzo"] for v in leggi_destinatari()["email"]
        if cadenza in v["cadenze"]
    ]
