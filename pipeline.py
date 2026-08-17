"""Pipeline di elaborazione dei file giornalieri delle navette.

Flusso: ZIP caricato -> analisi (:mod:`analisi`) -> file elaborato con i tre
parametri calcolati -> archiviazione su S3 -> report PDF -> email facoltativa.

Su S3 finiscono SIA l'archivio originale SIA l'elaborato: l'originale e' il dato
di sperimentazione da conservare, l'elaborato e' derivato e ricostruibile.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import analisi, posta, referto

DATI = Path(os.environ.get("TPL_DATI", "/var/lib/tpl-navette"))
UPLOAD = DATI / "uploads"
OUTPUT = DATI / "output"

# Prefissi nel bucket: l'originale resta separato dal derivato.
PREFISSO_ORIGINALI = "originali"
PREFISSO_ELABORATI = "elaborati"


def _s3():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT"],
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
            # Da botocore 1.36 ogni upload porta un checksum CRC32 che
            # l'OceanStor rifiuta ("Checksum algorithm provided is
            # unsupported... valid types: [SHA256, CRC32C]"): senza questo il
            # multipart fallisce su ogni file oltre gli 8 MB.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def carica_su_s3(percorso: Path, chiave_remota: Optional[str] = None) -> str:
    """Carica un file sul bucket del progetto. Restituisce la chiave remota."""
    bucket = os.environ["S3_BUCKET"]
    if chiave_remota is None:
        oggi = datetime.now().strftime("%Y/%m/%d")
        chiave_remota = f"{PREFISSO_ELABORATI}/{oggi}/{percorso.name}"
    _s3().upload_file(str(percorso), bucket, chiave_remota)
    return chiave_remota


def _chiave(prefisso: str, esito: analisi.Esito, nome: str) -> str:
    """Chiave S3 ordinata per navetta e giornata dei dati, non per data di carico.

    Cosi' i file di una stessa giornata restano vicini anche se caricati in
    ritardo o ricaricati.
    """
    giorno = (esito.data_file or datetime.now().strftime("%Y-%m-%d")).replace("-", "/")
    navetta = esito.navetta or "sconosciuta"
    return f"{prefisso}/{navetta}/{giorno}/{nome}"


def _originali(sorgente: Path) -> List[Path]:
    """I file da conservare tal quali: lo ZIP, oppure i tar.gz consegnati sciolti."""
    return (
        sorted(p for p in sorgente.iterdir() if p.is_file())
        if sorgente.is_dir()
        else [sorgente]
    )


def _radice(sorgente: Path) -> str:
    """Nome su cui costruire quelli dei file prodotti."""
    if sorgente.is_dir():
        primo = _originali(sorgente)[0].name
        # <navetta>_<data>_<VIN>_<data>_<ora>_pcN.tar.gz -> senza _pcN ne' estensione
        return re.sub(r"(_pc\d)?\.(tar\.gz|tgz|zip)$", "", primo, flags=re.IGNORECASE)
    return sorgente.stem


def elabora(percorso_caricato: Path, invia: bool = True) -> Dict[str, Any]:
    """Analizza l'archivio, ne archivia originale ed elaborato, produce il report.

    ``percorso_caricato`` puo' essere lo ZIP che raccoglie i due computer di
    bordo oppure la cartella in cui sono stati depositati i ``tar.gz`` sciolti.

    Un archivio di sola telemetria (senza il computer di localizzazione) non
    permette di calcolare i parametri cinematici: viene comunque analizzato e
    archiviato, e l'assenza risulta fra le anomalie.
    """
    OUTPUT.mkdir(parents=True, exist_ok=True)
    originali = _originali(percorso_caricato)
    radice = _radice(percorso_caricato)

    with tempfile.TemporaryDirectory(prefix="tpl-") as tmp:
        esito = analisi.analizza_archivio(percorso_caricato, Path(tmp))

        elaborato: Optional[Path] = None
        chiave_elaborato = ""
        try:
            elaborato = analisi.scrivi_elaborato(
                esito, OUTPUT / (radice + "-elaborato.h5")
            )
        except analisi.ArchivioNonValido as e:
            esito.anomalie.append(analisi.Anomalia("avviso", "elaborato", str(e)))

        chiavi = [
            carica_su_s3(p, _chiave(PREFISSO_ORIGINALI, esito, p.name))
            for p in originali
        ]
        chiave_originale = (
            chiavi[0].rsplit("/", 1)[0] + "/" if len(chiavi) > 1 else chiavi[0]
        )
        if elaborato:
            chiave_elaborato = carica_su_s3(
                elaborato, _chiave(PREFISSO_ELABORATI, esito, elaborato.name)
            )

        contesto: Dict[str, Any] = {
            "originale": ", ".join(p.name for p in originali),
            "elaborato": elaborato.name if elaborato else "",
            "chiave_s3": chiave_originale,
            "chiave_s3_elaborato": chiave_elaborato,
            "quando": datetime.now().strftime("%d/%m/%Y %H:%M"),
            **esito.come_dizionario(),
        }

        pdf = genera_pdf(contesto, OUTPUT / (radice + "-report.pdf"))
        contesto["pdf"] = pdf.name

        destinatari = [
            d.strip() for d in os.environ.get("MAIL_TO", "").split(",") if d.strip()
        ]
        if invia and destinatari:
            invia_email(
                destinatari,
                f"Navetta {esito.navetta} - dati del {esito.data_file}",
                _corpo_email(contesto),
                [pdf],
            )
        return contesto


def _corpo_email(contesto: Dict[str, Any]) -> str:
    righe = [
        f"Navetta: {contesto.get('navetta') or '-'}",
        f"Giornata: {contesto.get('data_file') or '-'}",
        f"Archivio: {contesto['originale']}",
        "",
        f"Originale archiviato in: {contesto['chiave_s3']}",
    ]
    if contesto.get("chiave_s3_elaborato"):
        righe.append(f"Elaborato archiviato in: {contesto['chiave_s3_elaborato']}")
    anomalie = contesto.get("anomalie") or []
    if anomalie:
        righe += ["", "Segnalazioni:"]
        righe += [
            f"  [{a['livello']}] {a['contesto']}: {a['messaggio']}" for a in anomalie
        ]
    righe += ["", "In allegato il report dell'elaborazione."]
    return "\n".join(righe)


def genera_pdf(contesto: Dict[str, Any], destinazione: Path) -> Path:
    """Report PDF dell'elaborazione. La composizione sta in :mod:`referto`."""
    from weasyprint import HTML

    destinazione.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=referto.componi(contesto)).write_pdf(str(destinazione))
    return destinazione


def invia_email(
    destinatari: List[str],
    oggetto: str,
    corpo: str,
    allegati: Optional[List[Path]] = None,
) -> None:
    """Invia il report di elaborazione."""
    posta.invia(destinatari, oggetto, corpo, allegati)
