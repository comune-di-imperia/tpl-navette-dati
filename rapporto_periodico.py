"""Rapporto periodico sulle statistiche, inviato per posta.

Si lancia dalla riga di comando, e da li' dal cron:

    cli rapporto-invia --cadenza settimanale   # il lunedi', settimana conclusa
    cli rapporto-invia --cadenza mensile       # il primo del mese

Il documento allegato e' lo stesso PDF che si scarica dalla pagina, limitato
al periodo e con in testa la sezione dello scostamento rispetto al periodo
precedente. Il corpo del messaggio riporta i numeri essenziali: chi apre la
posta al mattino deve capire come e' andata senza aprire l'allegato.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Tuple

from . import posta, rapporti, statistiche

logger = logging.getLogger("tpl.rapporto")

MESI = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre")


def intervallo(cadenza: str) -> Tuple[date, date]:
    if cadenza == "settimanale":
        return statistiche.settimana_scorsa()
    if cadenza == "mensile":
        return statistiche.mese_scorso()
    raise ValueError(f"cadenza sconosciuta: {cadenza}")


def descrizione(cadenza: str, da: date, a: date) -> str:
    if cadenza == "mensile":
        return f"{MESI[da.month - 1]} {da.year}"
    return f"dal {da.strftime('%d/%m')} al {a.strftime('%d/%m/%Y')}"


def variazione(voce: Dict[str, Any]) -> str:
    """Come si legge uno scostamento a parole."""
    if voce["verso"] == "pari":
        return "invariato"
    if voce["percentuale"] is None:
        return f"{voce['differenza']:+g} (prima nessun dato)"
    return f"{voce['differenza']:+g} ({voce['percentuale']:+g}%)"


def componi_messaggio(cadenza: str, dati: Dict[str, Any],
                      da: date, a: date) -> str:
    righe = [
        f"Rapporto {cadenza} della sperimentazione, "
        f"{descrizione(cadenza, da, a)}.",
        "",
        f"  Salite a bordo         {dati['corse']['totale']}",
        f"  Nuove registrazioni    {dati['registrazioni']['totale']}",
        f"  Valutazioni ricevute   {dati['voti']['totale']}",
    ]
    if dati["voti"]["media"]:
        righe.append(f"  Voto medio             {dati['voti']['media']} / 5")

    confronto = dati.get("confronto")
    if confronto:
        righe += [
            "",
            f"Rispetto al periodo precedente "
            f"({confronto['da'].strftime('%d/%m')} - "
            f"{confronto['a'].strftime('%d/%m')}):",
            "",
        ]
        for voce in confronto["voci"]:
            righe.append(f"  {voce['etichetta']:<24}{variazione(voce)}")

    senza_salite = [f["nome"] for f in dati.get("fermate", [])
                    if not f.get("quante")]
    if senza_salite:
        righe += ["", "Fermate senza salite nel periodo: "
                      + ", ".join(senza_salite) + "."]

    righe += [
        "",
        "Il documento allegato riporta tutti i numeri, la mappa delle fermate",
        "e la nota metodologica. I dati sono in forma anonima e non permettono",
        "di risalire ad alcuna persona.",
        "",
        "Sperimentazione trasporto pubblico a guida autonoma",
        "Comune di Imperia - decreto PCM/DTD n. 94/2026",
    ]
    return "\n".join(righe)


FONT = "Calibri, 'Segoe UI', Arial, sans-serif"
BLU = "#17408b"
VERDE = "#1b7f4b"
ROSSO = "#a8322c"
GRIGIO = "#6b7789"


def _colore(verso: str) -> str:
    return {"su": VERDE, "giu": ROSSO}.get(verso, GRIGIO)


def _freccia(verso: str) -> str:
    """Il verso si legge anche senza colore: c'e' chi stampa in bianco e nero
    e chi non distingue il rosso dal verde."""
    return {"su": "&#9650; ", "giu": "&#9660; "}.get(verso, "")


def _riquadro(cifra: str, che: str, sotto: str) -> str:
    return (
        f'<td width="50%" valign="top" style="padding:6px">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border:1px solid #d7e0ec;border-top:3px solid {BLU};'
        f'background:#fbfcfe">'
        f'<tr><td style="padding:14px 16px">'
        f'<div style="font-family:{FONT};font-size:30px;line-height:1.1;'
        f'font-weight:bold;color:{BLU}">{cifra}</div>'
        f'<div style="font-family:{FONT};font-size:14px;color:#39465a;'
        f'padding-top:3px">{che}</div>'
        f'<div style="font-family:{FONT};font-size:12px;color:{GRIGIO};'
        f'padding-top:2px">{sotto}</div>'
        f"</td></tr></table></td>"
    )


def _righe_confronto(voci, etichetta_colonna: str) -> str:
    righe = (
        f'<tr>'
        f'<th align="left" style="font-family:{FONT};font-size:11px;'
        f'letter-spacing:.6px;text-transform:uppercase;color:{GRIGIO};'
        f'font-weight:normal;padding:0 0 6px;border-bottom:2px solid #cfdaea">'
        f"{etichetta_colonna}</th>"
        f'<th align="right" style="font-family:{FONT};font-size:11px;'
        f'letter-spacing:.6px;text-transform:uppercase;color:{GRIGIO};'
        f'font-weight:normal;padding:0 0 6px;border-bottom:2px solid #cfdaea">'
        f"Prima</th>"
        f'<th align="right" style="font-family:{FONT};font-size:11px;'
        f'letter-spacing:.6px;text-transform:uppercase;color:{GRIGIO};'
        f'font-weight:normal;padding:0 0 6px;border-bottom:2px solid #cfdaea">'
        f"Ora</th>"
        f'<th align="right" style="font-family:{FONT};font-size:11px;'
        f'letter-spacing:.6px;text-transform:uppercase;color:{GRIGIO};'
        f'font-weight:normal;padding:0 0 6px;border-bottom:2px solid #cfdaea">'
        f"Variazione</th></tr>"
    )
    for voce in voci:
        nome = voce.get("etichetta") or voce.get("nome")
        righe += (
            f'<tr>'
            f'<td style="font-family:{FONT};font-size:14px;color:#232f3d;'
            f'padding:8px 0;border-bottom:1px solid #e8edf4">{nome}</td>'
            f'<td align="right" style="font-family:{FONT};font-size:14px;'
            f'color:{GRIGIO};padding:8px 10px;border-bottom:1px solid #e8edf4">'
            f'{voce["prima"]}</td>'
            f'<td align="right" style="font-family:{FONT};font-size:14px;'
            f'color:#232f3d;font-weight:bold;padding:8px 10px;'
            f'border-bottom:1px solid #e8edf4">{voce["adesso"]}</td>'
            f'<td align="right" style="font-family:{FONT};font-size:14px;'
            f'font-weight:bold;white-space:nowrap;color:{_colore(voce["verso"])};'
            f'padding:8px 0;border-bottom:1px solid #e8edf4">'
            f"{_freccia(voce['verso'])}{variazione(voce)}</td></tr>"
        )
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f"{righe}</table>"
    )


def componi_html(cadenza: str, dati: Dict[str, Any], da: date, a: date) -> str:
    """Versione impaginata del messaggio.

    Tabelle e stili in linea, non fogli di stile ne' riquadri flessibili:
    Outlook usa il motore di Word e di tutto il resto non tiene conto. Gli
    stemmi viaggiano dentro il messaggio, perche' il sito e' ad accesso
    riservato e un'immagine presa da li' non si vedrebbe.
    """
    periodo = descrizione(cadenza, da, a)
    voti = dati["voti"]
    confronto = dati.get("confronto")

    riquadri = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f"<tr>"
        + _riquadro(str(dati["corse"]["totale"]), "salite a bordo",
                    f"in {dati['corse']['giorni_di_servizio']} giorni di esercizio")
        + _riquadro(str(dati["registrazioni"]["totale"]), "nuove registrazioni",
                    "persone iscritte nel periodo")
        + "</tr><tr>"
        + _riquadro(str(voti["totale"]), "valutazioni raccolte",
                    f"{voti['con_commento']} con un commento")
        + _riquadro(
            (f"{voti['media']}<span style=\"font-size:17px;color:{GRIGIO}\">"
             f" / 5</span>") if voti["media"] else "&mdash;",
            "voto medio",
            "giudizio di chi ha viaggiato")
        + "</tr></table>"
    )

    corpo = ""
    if confronto:
        corpo += (
            f'<div style="font-family:{FONT};font-size:17px;font-weight:bold;'
            f'color:{BLU};padding:26px 0 4px">'
            f"Come &egrave; andata rispetto a prima</div>"
            f'<div style="font-family:{FONT};font-size:13px;color:{GRIGIO};'
            f'padding-bottom:12px">Confronto con il periodo di pari durata '
            f'precedente, dal {confronto["da"].strftime("%d/%m/%Y")} al '
            f'{confronto["a"].strftime("%d/%m/%Y")}.</div>'
            + _righe_confronto(confronto["voci"], "Grandezza")
        )
        if confronto.get("fermate"):
            corpo += (
                f'<div style="font-family:{FONT};font-size:17px;'
                f'font-weight:bold;color:{BLU};padding:26px 0 12px">'
                f"Dove sale la gente</div>"
                + _righe_confronto(confronto["fermate"], "Fermata")
            )

    senza_salite = [f["nome"] for f in dati.get("fermate", [])
                    if not f.get("quante")]
    if senza_salite:
        corpo += (
            f'<div style="font-family:{FONT};font-size:13px;color:#7a5b12;'
            f'background:#fff8e6;border-left:3px solid #e0b530;'
            f'padding:11px 14px;margin-top:22px">'
            f"Nel periodo non &egrave; salito nessuno a: "
            f'<b>{", ".join(senza_salite)}</b>.</div>'
        )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#eef1f6">
<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#eef1f6">
<tr><td align="center" style="padding:24px 12px">

<table width="620" cellpadding="0" cellspacing="0" border="0"
       style="max-width:620px;background:#ffffff;border:1px solid #dde4ee">

  <tr><td style="background:{BLU};padding:20px 26px">
    <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
      <td width="52" valign="middle">
        <img src="cid:stemma" width="42" alt="Comune di Imperia"
             style="display:block;border:0">
      </td>
      <td valign="middle">
        <div style="font-family:{FONT};font-size:11px;letter-spacing:2.4px;
                    text-transform:uppercase;color:#a9c4e8">
          Comune di Imperia &middot; Sperimentazione
        </div>
        <div style="font-family:{FONT};font-size:21px;font-weight:bold;
                    color:#ffffff;padding-top:2px">
          Bus a guida autonoma
        </div>
      </td>
    </tr></table>
  </td></tr>

  <tr><td style="padding:26px 26px 0">
    <div style="font-family:{FONT};font-size:24px;font-weight:bold;
                color:{BLU};line-height:1.2">
      Rapporto {cadenza}
    </div>
    <div style="font-family:{FONT};font-size:15px;color:#4a5768;
                padding:4px 0 18px">
      {periodo.capitalize()}
    </div>
    {riquadri}
    {corpo}
  </td></tr>

  <tr><td style="padding:28px 26px 26px">
    <div style="font-family:{FONT};font-size:14px;color:#4a5768;
                text-align:center;line-height:1.5">
      Nel documento allegato trovi tutti i numeri, la mappa delle fermate con
      le salite e la nota metodologica.
    </div>
  </td></tr>

  <tr><td style="background:#f6f8fb;border-top:1px solid #e3e9f1;
                 padding:18px 26px">
    <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
      <td valign="middle">
        <div style="font-family:{FONT};font-size:12px;color:{GRIGIO};
                    line-height:1.5">
          I dati provengono dall'applicazione dei passeggeri in
          <b>forma anonima</b> e non permettono di risalire ad alcuna persona.<br>
          Sperimentazione ai sensi del decreto PCM/DTD n. 94/2026
        </div>
      </td>
      <td width="70" align="right" valign="middle">
        <img src="cid:kore" width="58" alt="Universit&agrave; KORE"
             style="display:block;border:0">
      </td>
    </tr></table>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""


def stemmi() -> Dict[str, Path]:
    from .app import app

    statica = Path(app.static_folder)
    return {"stemma": statica / "imperia.png", "kore": statica / "kore.png"}


def genera_pdf(dati: Dict[str, Any], destinazione: Path) -> None:
    from flask import render_template
    from weasyprint import HTML

    from .app import app

    with app.app_context():
        documento = render_template("statistiche_pdf.html", dati=dati)
    # Gli stemmi e la mappa sono file locali: senza base l'immagine non si
    # risolve e il documento esce senza testata.
    base = str(Path(app.static_folder).resolve()) + "/"
    HTML(string=documento, base_url=base).write_pdf(str(destinazione))


def invia(cadenza: str, destinatari: List[str] = None) -> int:
    """Prepara e spedisce il rapporto. Restituisce quanti l'hanno ricevuto.

    Nessun destinatario non e' un errore: l'elenco si governa dall'interfaccia,
    e vuoto vuol dire che per ora quel rapporto non serve a nessuno.
    """
    if destinatari is None:
        destinatari = rapporti.per_cadenza(cadenza)
    if not destinatari:
        logger.info("Rapporto senza destinatari: non spedito",
                    extra={"context": {"cadenza": cadenza}})
        return 0

    da, a = intervallo(cadenza)
    dati = statistiche.raccogli(periodo=(da, a))

    with tempfile.TemporaryDirectory() as cartella:
        allegato = Path(cartella) / f"statistiche-{cadenza}-{da.isoformat()}.pdf"
        genera_pdf(dati, allegato)
        posta.invia(
            destinatari=destinatari,
            oggetto=(f"Sperimentazione bus a guida autonoma - rapporto "
                     f"{cadenza}, {descrizione(cadenza, da, a)}"),
            corpo=componi_messaggio(cadenza, dati, da, a),
            allegati=[allegato],
            html=componi_html(cadenza, dati, da, a),
            immagini=stemmi(),
        )

    logger.info("Rapporto periodico inviato",
                extra={"context": {"cadenza": cadenza,
                                   "destinatari": len(destinatari),
                                   "da": da.isoformat(), "a": a.isoformat()}})
    return len(destinatari)
