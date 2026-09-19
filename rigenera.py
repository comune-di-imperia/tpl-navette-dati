"""Rifa' i referti gia' prodotti con l'impaginazione corrente.

Quando l'impaginazione cambia — una grandezza che si toglie, una dicitura che
si corregge — i documenti gia' consegnati restano indietro, e in mano al
committente si trovano due versioni dello stesso giorno che dicono cose
diverse. Rigenerarli e' l'unico modo per avere un fascicolo coerente.

Perche' sia possibile senza rileggere gli archivi originali, che stanno su S3 e
pesano decine di megabyte l'uno, l'elaborazione salva accanto al referto il
**contesto** da cui il referto nasce: riepiloghi, anomalie e riferimenti
d'archivio, in un file JSON di pochi kilobyte. Rifare il documento e' allora
solo ricomporre l'HTML e ristamparlo.

    cli referti-rigenera --prova      # dice cosa farebbe, non tocca nulla
    cli referti-rigenera              # rifa' tutti i referti
    cli referti-rigenera --solo BF05_20260915_...

Il documento sostituito non si perde: prima di riscriverlo se ne mette una
copia in ``referti-sostituiti/<data>/``. Un referto e' la prova di
un'elaborazione, e una prova non si sovrascrive senza lasciarne traccia.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("tpl.rigenera")

SUFFISSO_CONTESTO = "-contesto.json"
SUFFISSO_REFERTO = "-report.pdf"


def _cartella_referti() -> Path:
    from . import pipeline

    return pipeline.OUTPUT


def cartella_sostituiti(quando: Optional[date] = None) -> Path:
    """Dove finiscono i referti che vengono rimpiazzati.

    Fuori dalla cartella dei referti: li' dentro l'applicazione cerca i
    documenti da far scaricare, e una copia vecchia con lo stesso nome in una
    sottocartella e' un equivoco che prima o poi qualcuno paga.
    """
    quando = quando or date.today()
    return _cartella_referti().parent / "referti-sostituiti" / quando.isoformat()


def _semplice(valore: Any) -> Any:
    """Numeri di numpy riportati a numeri di Python.

    Un numero salvato come testo tornerebbe indietro come testo, e
    l'impaginazione del referto formatta solo cio' che riconosce come numero:
    ``17.759`` invece di ``17,759``. Si converte alla scrittura, dove il tipo
    si sa ancora.
    """
    if isinstance(valore, dict):
        return {k: _semplice(v) for k, v in valore.items()}
    if isinstance(valore, (list, tuple)):
        return [_semplice(v) for v in valore]
    if hasattr(valore, "item") and not isinstance(valore, (str, bytes)):
        try:
            return valore.item()
        except (AttributeError, ValueError):
            return valore
    return valore


def contesto_da_salvare(contesto: Dict[str, Any]) -> Dict[str, Any]:
    """Il contesto ripulito di cio' che non si puo' scrivere in un JSON.

    Sotto ``computer`` l'analisi tiene anche i dati grezzi, in chiavi che
    cominciano con l'underscore: al referto non servono e peserebbero quanto
    l'archivio di partenza.
    """
    ripulito = {k: v for k, v in contesto.items() if k != "computer"}
    computer = {}
    for pc, info in (contesto.get("computer") or {}).items():
        computer[pc] = {
            chiave: valore
            for chiave, valore in info.items()
            if not chiave.startswith("_")
        }
    ripulito["computer"] = computer
    return _semplice(ripulito)


def salva_contesto(contesto: Dict[str, Any], radice: str) -> Path:
    """Scrive il contesto accanto al referto, per poterlo rifare domani."""
    percorso = _cartella_referti() / f"{radice}{SUFFISSO_CONTESTO}"
    percorso.write_text(
        json.dumps(contesto_da_salvare(contesto), indent=1, ensure_ascii=False,
                   default=str),
        encoding="utf-8",
    )
    return percorso


def leggi_contesto(radice: str) -> Optional[Dict[str, Any]]:
    percorso = _cartella_referti() / f"{radice}{SUFFISSO_CONTESTO}"
    if not percorso.exists():
        return None
    try:
        return json.loads(percorso.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Contesto illeggibile",
                       extra={"context": {"radice": radice}})
        return None


def elencabili() -> List[str]:
    """Le radici per cui esiste sia il contesto sia il referto da rifare."""
    cartella = _cartella_referti()
    radici = []
    for contesto in sorted(cartella.glob(f"*{SUFFISSO_CONTESTO}")):
        radice = contesto.name[: -len(SUFFISSO_CONTESTO)]
        if (cartella / f"{radice}{SUFFISSO_REFERTO}").exists():
            radici.append(radice)
    return radici


def rigenera(radice: str, prova: bool = False,
             quando: Optional[date] = None) -> Dict[str, Any]:
    """Rifa' un referto. Restituisce cosa e' stato fatto."""
    from . import pipeline

    cartella = _cartella_referti()
    referto = cartella / f"{radice}{SUFFISSO_REFERTO}"
    contesto = leggi_contesto(radice)

    if contesto is None:
        return {"radice": radice, "esito": "senza contesto"}
    if not referto.exists():
        return {"radice": radice, "esito": "referto assente"}
    if prova:
        return {"radice": radice, "esito": "da rifare"}

    copia = cartella_sostituiti(quando)
    copia.mkdir(parents=True, exist_ok=True)
    # Una copia gia' presente non si tocca: e' il documento come fu consegnato.
    # Rigenerando due volte nello stesso giorno, la seconda sovrascriverebbe
    # l'originale con una versione gia' corretta, e la prova andrebbe persa.
    if not (copia / referto.name).exists():
        shutil.copy2(referto, copia / referto.name)

    prima = referto.stat().st_size
    pipeline.genera_pdf(contesto, referto)
    return {
        "radice": radice,
        "esito": "rifatto",
        "prima_byte": prima,
        "dopo_byte": referto.stat().st_size,
        "copia": str(copia / referto.name),
    }


def rigenera_tutti(solo: Optional[List[str]] = None,
                   prova: bool = False) -> List[Dict[str, Any]]:
    quando = date.today()
    radici = solo if solo else elencabili()
    esiti = [rigenera(r, prova=prova, quando=quando) for r in radici]
    logger.info(
        "Referti rigenerati",
        extra={"context": {
            "esaminati": len(esiti),
            "rifatti": sum(1 for e in esiti if e["esito"] == "rifatto"),
            "prova": prova,
        }},
    )
    return esiti
