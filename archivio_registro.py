"""Archiviazione mensile del registro attivita' sul bucket del progetto.

Due passi indipendenti, in quest'ordine:

1. **copia** su S3 di ogni mese concluso che non sia gia' archiviato;
2. **eliminazione** dal database dei mesi oltre la finestra di consultazione,
   ma soltanto se la copia esiste davvero sul bucket.

Tenerli separati significa che su S3 c'e' sempre tutto lo storico, mentre nel
database restano solo i mesi che servono a video: se il VPS si perde, il
registro non si perde con lui.

L'invariante da non violare mai: **non si elimina nulla che non sia gia' stato
verificato sul bucket**.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from . import db, pipeline

logger = logging.getLogger("tpl.archivio")

PREFISSO = "registro"

# Mesi che restano consultabili dall'applicazione. Il riferimento sono i 6 mesi
# del provvedimento del Garante sugli amministratori di sistema (27/11/2008),
# raddoppiati per margine; su S3 la conservazione non ha scadenza.
MESI_IN_LINEA = int(os.environ.get("TPL_REGISTRO_MESI", "12"))


def chiave(mese: str) -> str:
    return f"{PREFISSO}/{mese}.jsonl.gz"


def _esiste(s3, bucket: str, mese: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        s3.head_object(Bucket=bucket, Key=chiave(mese))
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def _confeziona(voci: List[Dict[str, Any]]) -> bytes:
    """JSON Lines compresso: una riga per voce, leggibile con zcat senza
    strumenti nostri, e concatenabile."""
    memoria = io.BytesIO()
    with gzip.GzipFile(fileobj=memoria, mode="wb", mtime=0) as g:
        for v in voci:
            g.write(json.dumps(v, ensure_ascii=False, default=str).encode() + b"\n")
    return memoria.getvalue()


def _mesi_da_eliminare(mesi: List[str], mesi_in_linea: int) -> List[str]:
    """Mesi oltre la finestra di consultazione."""
    oggi = datetime.now(timezone.utc)
    soglia = (oggi.year * 12 + oggi.month - 1) - mesi_in_linea
    fuori = []
    for m in mesi:
        anno, mese = (int(x) for x in m.split("-"))
        if (anno * 12 + mese - 1) <= soglia:
            fuori.append(m)
    return fuori


def archivia(
    mesi_in_linea: int = MESI_IN_LINEA, elimina: bool = True
) -> Dict[str, Any]:
    """Esegue copia ed eventuale eliminazione. Ritorna il riepilogo."""
    bucket = os.environ["S3_BUCKET"]
    s3 = pipeline._s3()

    mesi = db.mesi_registro()
    esito: Dict[str, Any] = {
        "esaminati": mesi,
        "caricati": [],
        "gia_presenti": [],
        "eliminati": {},
        "saltati": [],
    }

    for mese in mesi:
        if _esiste(s3, bucket, mese):
            esito["gia_presenti"].append(mese)
            continue
        voci = db.leggi_registro_mese(mese)
        if not voci:
            continue
        s3.put_object(
            Bucket=bucket,
            Key=chiave(mese),
            Body=_confeziona(voci),
            ContentType="application/gzip",
        )
        logger.info(
            "mese archiviato",
            extra={"mese": mese, "voci": len(voci), "chiave": chiave(mese)},
        )
        esito["caricati"].append(mese)

    if not elimina:
        return esito

    for mese in _mesi_da_eliminare(mesi, mesi_in_linea):
        # rilettura dal bucket, non fiducia in quanto fatto poche righe sopra:
        # e' l'ultimo controllo prima di una cancellazione irreversibile
        if not _esiste(s3, bucket, mese):
            esito["saltati"].append(mese)
            logger.warning("copia assente, mese non eliminato", extra={"mese": mese})
            continue
        esito["eliminati"][mese] = db.elimina_registro_mese(mese)

    return esito
