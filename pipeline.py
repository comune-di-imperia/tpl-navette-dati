"""Pipeline di elaborazione dei file giornalieri delle navette.

Flusso: ZIP caricato -> analisi (:mod:`analisi`) -> file elaborato con i tre
parametri calcolati -> archiviazione su S3 -> report PDF -> email facoltativa.

Su S3 finiscono SIA l'archivio originale SIA l'elaborato: l'originale e' il dato
di sperimentazione da conservare, l'elaborato e' derivato e ricostruibile.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import analisi, posta

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


def elabora(percorso_caricato: Path, invia: bool = True) -> Dict[str, Any]:
    """Analizza l'archivio, ne archivia originale ed elaborato, produce il report.

    Un archivio di sola telemetria (senza il computer di localizzazione) non
    permette di calcolare i parametri cinematici: viene comunque analizzato e
    archiviato, e l'assenza risulta fra le anomalie.
    """
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tpl-") as tmp:
        esito = analisi.analizza_archivio(percorso_caricato, Path(tmp))

        elaborato: Optional[Path] = None
        chiave_elaborato = ""
        try:
            elaborato = analisi.scrivi_elaborato(
                esito, OUTPUT / (percorso_caricato.stem + "-elaborato.h5")
            )
        except analisi.ArchivioNonValido as e:
            esito.anomalie.append(analisi.Anomalia("avviso", "elaborato", str(e)))

        chiave_originale = carica_su_s3(
            percorso_caricato,
            _chiave(PREFISSO_ORIGINALI, esito, percorso_caricato.name),
        )
        if elaborato:
            chiave_elaborato = carica_su_s3(
                elaborato, _chiave(PREFISSO_ELABORATI, esito, elaborato.name)
            )

        contesto: Dict[str, Any] = {
            "originale": percorso_caricato.name,
            "elaborato": elaborato.name if elaborato else "",
            "chiave_s3": chiave_originale,
            "chiave_s3_elaborato": chiave_elaborato,
            "quando": datetime.now().strftime("%d/%m/%Y %H:%M"),
            **esito.come_dizionario(),
        }

        pdf = genera_pdf(contesto, OUTPUT / (percorso_caricato.stem + "-report.pdf"))
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


def _tabella(titolo: str, voci: Dict[str, Any]) -> str:
    if not voci:
        return ""
    righe = "".join(
        f'<tr><td class="k">{k.replace("_", " ")}</td><td>{v}</td></tr>'
        for k, v in voci.items()
    )
    return f'<table><tr><th colspan="2">{titolo}</th></tr>{righe}</table>'


def genera_pdf(contesto: Dict[str, Any], destinazione: Path) -> Path:
    """Report PDF neutro, senza carta intestata."""
    from weasyprint import HTML

    corpo = []
    for pc, info in (contesto.get("computer") or {}).items():
        corpo.append(
            _tabella(
                f"Computer di bordo {pc} &ndash; {info.get('tipo', '?')}",
                info.get("riepilogo") or {},
            )
        )
    if not corpo:
        corpo.append('<p class="vuoto">nessun dato analizzabile nell\'archivio</p>')

    anomalie = contesto.get("anomalie") or []
    if anomalie:
        righe = "".join(
            f'<tr><td class="k">{a["livello"]} &ndash; {a["contesto"]}</td>'
            f"<td>{a['messaggio']}</td></tr>"
            for a in anomalie
        )
        corpo.append(
            f'<table><tr><th colspan="2">Segnalazioni</th></tr>{righe}</table>'
        )

    html = """<!DOCTYPE html><html lang="it"><head><meta charset="utf-8">
<style>
@page { size: A4; margin: 2cm; }
body { font-family: "DejaVu Sans", sans-serif; font-size: 10pt; color: #1b2430; }
h1 { font-size: 14pt; margin: 0 0 4px; }
.sub { color: #667; font-size: 9pt; margin-bottom: 18px; }
table { border-collapse: collapse; width: 100%; margin-top: 12px; font-size: 9pt; }
th { background: #eef2f7; text-align: left; padding: 5px 7px; }
td { border: 1px solid #d8e0ea; padding: 4px 7px; }
td.k { width: 45%; font-weight: bold; background: #fafbfd; }
.vuoto { color: #888; font-style: italic; }
.meta { margin-top: 20px; font-size: 8.5pt; color: #555; }
</style></head><body>
<h1>Report elaborazione dati navetta __NAVETTA__</h1>
<div class="sub">TPL navette a guida autonoma &ndash; Comune di Imperia
&nbsp;|&nbsp; giornata __GIORNO__ &nbsp;|&nbsp; VIN __VIN__</div>
__CORPO__
<div class="meta">
  Archivio originale: __ORIGINALE__<br>
  File elaborato: __ELABORATO__<br>
  Archiviazione S3: __CHIAVE__<br>
  Elaborazione del __QUANDO__
</div>
</body></html>"""
    for segna, valore in (
        ("__NAVETTA__", contesto.get("navetta") or "-"),
        ("__GIORNO__", contesto.get("data_file") or "-"),
        ("__VIN__", contesto.get("vin") or "-"),
        ("__CORPO__", "\n".join(corpo)),
        ("__ORIGINALE__", contesto.get("originale", "-")),
        ("__ELABORATO__", contesto.get("elaborato") or "-"),
        ("__CHIAVE__", contesto.get("chiave_s3", "-")),
        ("__QUANDO__", contesto.get("quando", "-")),
    ):
        html = html.replace(segna, str(valore))

    destinazione.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html).write_pdf(str(destinazione))
    return destinazione


def invia_email(
    destinatari: List[str],
    oggetto: str,
    corpo: str,
    allegati: Optional[List[Path]] = None,
) -> None:
    """Invia il report di elaborazione."""
    posta.invia(destinatari, oggetto, corpo, allegati)
