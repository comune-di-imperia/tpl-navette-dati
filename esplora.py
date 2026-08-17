"""Esplorazione in sola lettura del bucket S3 del progetto.

Elenca oggetti e "cartelle" (i prefissi separati da ``/``) e ne consente lo
scarico. Non espone nessuna operazione di scrittura: da qui non si cancella e
non si carica nulla, perche' il bucket contiene i dati di sperimentazione, che
sono il documento originale.

Lo scarico passa dall'applicazione invece che da un collegamento firmato: cosi'
le chiavi restano sul server e ogni prelievo finisce nel registro.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import pipeline

# Prefissi che non tutti possono vedere: il registro contiene gli accessi.
# La chiave e' il prefisso, il valore il permesso richiesto.
PREFISSI_RISERVATI = {"registro/": "registro.leggi"}

MASSIMO_PER_PAGINA = 200


def permesso_richiesto(chiave: str) -> Optional[str]:
    """Permesso necessario per vedere questa chiave o prefisso, se ce n'e' uno."""
    for prefisso, permesso in PREFISSI_RISERVATI.items():
        if chiave.startswith(prefisso):
            return permesso
    return None


def _pulisci(prefisso: str) -> str:
    """Normalizza il prefisso richiesto.

    Le chiavi S3 sono piatte, quindi non esiste una risalita alla Bill Gates
    con "..": si tolgono comunque, insieme agli slash iniziali, perche' un
    prefisso strano produce solo elenchi vuoti e confusione.
    """
    # prima si tolgono i punti, poi gli slash: nell'ordine inverso "../x"
    # lascerebbe "//x", che e' inoffensivo ma sporco
    p = (prefisso or "").replace("..", "").lstrip("/")
    if p and not p.endswith("/"):
        p += "/"
    return p


def briciole(prefisso: str) -> List[Dict[str, str]]:
    """Percorso navigabile: [{nome, prefisso}] dalla radice al punto corrente."""
    voci = [{"nome": "archivio", "prefisso": ""}]
    percorso = ""
    for pezzo in [p for p in prefisso.split("/") if p]:
        percorso += pezzo + "/"
        voci.append({"nome": pezzo, "prefisso": percorso})
    return voci


def elenca(prefisso: str = "", segue: str = "") -> Dict[str, Any]:
    """Contenuto di un livello: sottocartelle e oggetti.

    ``Delimiter='/'`` fa restituire a S3 i prefissi comuni invece di tutte le
    chiavi ricorsivamente: e' quello che rende la navigazione a cartelle
    possibile su uno spazio che di cartelle non ne ha.
    """
    prefisso = _pulisci(prefisso)
    bucket = os.environ["S3_BUCKET"]
    parametri: Dict[str, Any] = {
        "Bucket": bucket,
        "Prefix": prefisso,
        "Delimiter": "/",
        "MaxKeys": MASSIMO_PER_PAGINA,
    }
    if segue:
        parametri["ContinuationToken"] = segue

    risposta = pipeline._s3().list_objects_v2(**parametri)

    cartelle = [
        {
            "prefisso": c["Prefix"],
            "nome": c["Prefix"][len(prefisso) :].rstrip("/"),
        }
        for c in risposta.get("CommonPrefixes", [])
    ]
    oggetti = [
        {
            "chiave": o["Key"],
            "nome": o["Key"][len(prefisso) :],
            "dimensione": o["Size"],
            "modificato": o["LastModified"],
        }
        for o in risposta.get("Contents", [])
        # la "cartella" stessa puo' comparire come oggetto vuoto: non e' un file
        if o["Key"] != prefisso
    ]

    return {
        "prefisso": prefisso,
        "briciole": briciole(prefisso),
        "cartelle": cartelle,
        "oggetti": oggetti,
        "segue": risposta.get("NextContinuationToken", ""),
        "troncato": risposta.get("IsTruncated", False),
    }


def dettagli(chiave: str) -> Optional[Dict[str, Any]]:
    """Metadati di un oggetto, o None se non esiste."""
    from botocore.exceptions import ClientError

    try:
        r = pipeline._s3().head_object(Bucket=os.environ["S3_BUCKET"], Key=chiave)
    except ClientError:
        return None
    return {
        "chiave": chiave,
        "dimensione": r["ContentLength"],
        "modificato": r["LastModified"],
        "tipo": r.get("ContentType", "application/octet-stream"),
    }


def flusso(chiave: str, blocco: int = 1 << 20):
    """Contenuto dell'oggetto a blocchi, per non tenere in memoria 90 MB."""
    corpo = pipeline._s3().get_object(Bucket=os.environ["S3_BUCKET"], Key=chiave)[
        "Body"
    ]
    try:
        while True:
            pezzo = corpo.read(blocco)
            if not pezzo:
                return
            yield pezzo
    finally:
        corpo.close()
