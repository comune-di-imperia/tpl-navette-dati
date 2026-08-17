"""Composizione del report PDF di elaborazione.

Il report va in mano al committente, quindi le grandezze compaiono con nome
esteso e unita' di misura: le chiavi tecniche del riepilogo (``velocita_media_kmh``)
restano nei dati, non sulla carta.
"""

from __future__ import annotations

import html as _html
from typing import Any, Dict, List, Tuple

# (chiave, etichetta, unita', decimali). L'ordine e' quello di lettura, non
# quello in cui i valori vengono calcolati.
ETICHETTE: Tuple[Tuple[str, str, str, int], ...] = (
    # percorrenza e cinematica
    ("distanza_totale_km", "Distanza percorsa", "km", 3),
    ("percorrenza_odometro_km", "Percorrenza da odometro", "km", 3),
    ("odometro_iniziale_km", "Odometro iniziale", "km", 3),
    ("odometro_finale_km", "Odometro finale", "km", 3),
    ("velocita_media_kmh", "Velocita' media in marcia", "km/h", 2),
    ("velocita_massima_kmh", "Velocita' massima", "km/h", 2),
    ("tempo_in_moto_ore", "Tempo in movimento", "h", 2),
    ("durata_ore", "Arco temporale dei dati", "h", 2),
    ("accelerazione_massima_mps2", "Accelerazione massima", "m/s²", 3),
    ("decelerazione_massima_mps2", "Decelerazione massima", "m/s²", 3),
    ("accelerazione_media_mps2", "Accelerazione media", "m/s²", 3),
    ("decelerazione_media_mps2", "Decelerazione media", "m/s²", 3),
    ("giri_medi_rpm", "Regime medio", "giri/min", 1),
    ("giri_massimi_rpm", "Regime massimo", "giri/min", 1),
    ("giri_riferiti_a", "Regime riferito a", "", -1),
    ("raggio_ruota_m", "Raggio ruota adottato", "m", 3),
    # assetto
    ("pitch_beccheggio_medio_gradi", "Beccheggio medio", "°", 2),
    ("pitch_beccheggio_minimo_gradi", "Beccheggio minimo", "°", 2),
    ("pitch_beccheggio_massimo_gradi", "Beccheggio massimo", "°", 2),
    ("rollio_medio_gradi", "Rollio medio", "°", 2),
    ("rollio_minimo_gradi", "Rollio minimo", "°", 2),
    ("rollio_massimo_gradi", "Rollio massimo", "°", 2),
    # esercizio
    ("batteria_iniziale_pct", "Batteria a inizio giornata", "%", 1),
    ("batteria_finale_pct", "Batteria a fine giornata", "%", 1),
    ("batteria_minima_pct", "Batteria al minimo", "%", 1),
    ("aperture_porte", "Aperture porte", "", 0),
    ("ore_veicolo_operativo", "Veicolo operativo", "h", 2),
    ("ore_veicolo_in_sosta", "Veicolo in sosta", "h", 2),
    ("temperatura_cabina_media", "Temperatura cabina media", "°C", 1),
    ("temperatura_cabina_max", "Temperatura cabina massima", "°C", 1),
    ("temperatura_esterna_media", "Temperatura esterna media", "°C", 1),
    ("temperatura_esterna_max", "Temperatura esterna massima", "°C", 1),
    ("temperatura_motore_media", "Temperatura motore media", "°C", 1),
    ("temperatura_motore_max", "Temperatura motore massima", "°C", 1),
    # localizzazione
    ("campioni", "Campioni elaborati", "", 0),
    ("sessioni", "Sessioni di marcia", "", 0),
    ("dal", "Primo campione", "", -1),
    ("al", "Ultimo campione", "", -1),
    ("campioni_scartati", "Campioni scartati", "", 0),
    ("hit_ratio_medio", "Qualita' del posizionamento (media)", "", 4),
    ("hit_ratio_minimo", "Qualita' del posizionamento (minimo)", "", 4),
    ("eta_correzioni_gnss_media_s", "Eta' media correzioni GNSS", "s", 3),
    ("eta_correzioni_gnss_max_s", "Eta' massima correzioni GNSS", "s", 1),
    ("latitudine_media", "Latitudine media", "", 6),
    ("longitudine_media", "Longitudine media", "", 6),
)

# Le chiavi della ripartizione autonoma/manuale hanno un blocco tutto loro.
CHIAVI_GUIDA = (
    "guida_autonoma_km",
    "guida_manuale_km",
    "guida_autonoma_ore",
    "guida_manuale_ore",
    "quota_autonoma_percorrenza_pct",
    "quota_autonoma_tempo_pct",
)

TIPI = {
    "telemetria": "telemetria di bordo",
    "localizzazione": "localizzazione e navigazione",
}


def _e(testo: Any) -> str:
    return _html.escape(str(testo))


def _numero(valore: Any, decimali: int) -> str:
    """Formato italiano: virgola decimale e punto per le migliaia."""
    if decimali < 0 or not isinstance(valore, (int, float)) or isinstance(valore, bool):
        return _e(valore)
    testo = f"{valore:,.{decimali}f}"
    return testo.replace(",", " ").replace(".", ",").replace(" ", ".")


def _riquadro(valore: str, unita: str, etichetta: str) -> str:
    return (
        f'<div class="riquadro"><div class="valore">{valore}'
        f'<span class="unita">{unita}</span></div>'
        f'<div class="etichetta">{etichetta}</div></div>'
    )


def _in_evidenza(riepilogo: Dict[str, Any]) -> str:
    """I quattro numeri che si guardano per primi."""
    scelte = [
        ("distanza_totale_km", "km", "Distanza percorsa", 3),
        ("percorrenza_odometro_km", "km", "Percorrenza", 3),
        ("tempo_in_moto_ore", "h", "In movimento", 2),
        ("velocita_media_kmh", "km/h", "Velocita' media", 1),
        ("velocita_massima_kmh", "km/h", "Velocita' massima", 1),
        ("batteria_finale_pct", "%", "Batteria finale", 0),
    ]
    riquadri, viste = [], set()
    for chiave, unita, etichetta, dec in scelte:
        if chiave not in riepilogo or etichetta in viste:
            continue
        # distanza e percorrenza dicono la stessa cosa: basta la prima presente
        if etichetta in ("Distanza percorsa", "Percorrenza") and viste & {
            "Distanza percorsa",
            "Percorrenza",
        }:
            continue
        riquadri.append(_riquadro(_numero(riepilogo[chiave], dec), unita, etichetta))
        viste.add(etichetta)
        if len(riquadri) == 4:
            break
    return f'<div class="riquadri">{"".join(riquadri)}</div>' if riquadri else ""


def _barra_guida(riepilogo: Dict[str, Any]) -> str:
    """Ripartizione autonoma/manuale: una barra vale piu' di due percentuali."""
    if "guida_autonoma_km" not in riepilogo:
        return ""

    auto_km = riepilogo.get("guida_autonoma_km", 0.0)
    man_km = riepilogo.get("guida_manuale_km", 0.0)
    auto_h = riepilogo.get("guida_autonoma_ore", 0.0)
    man_h = riepilogo.get("guida_manuale_ore", 0.0)
    quota = riepilogo.get("quota_autonoma_percorrenza_pct")
    if quota is None:
        return ""
    resto = round(100.0 - quota, 1)

    return f"""
<h2>Guida autonoma e guida manuale</h2>
<div class="barra">
  <div class="parte autonoma" style="width: {max(quota, 0.5)}%">{_numero(quota, 1)}%</div>
  <div class="parte manuale" style="width: {max(resto, 0.5)}%">{_numero(resto, 1)}%</div>
</div>
<table class="dati">
  <thead><tr><th>Modalita'</th><th class="destra">Percorrenza</th>
    <th class="destra">Tempo in marcia</th><th class="destra">Quota</th></tr></thead>
  <tbody>
    <tr><td><span class="pallino autonoma"></span> Autonoma</td>
        <td class="destra">{_numero(auto_km, 3)} km</td>
        <td class="destra">{_numero(auto_h, 2)} h</td>
        <td class="destra"><strong>{_numero(quota, 1)}%</strong></td></tr>
    <tr><td><span class="pallino manuale"></span> Manuale</td>
        <td class="destra">{_numero(man_km, 3)} km</td>
        <td class="destra">{_numero(man_h, 2)} h</td>
        <td class="destra"><strong>{_numero(resto, 1)}%</strong></td></tr>
  </tbody>
</table>
<p class="nota">Quota calcolata sulla percorrenza. Il tempo considera i soli
campioni in movimento: le soste non sono attribuite a nessuna delle due
modalita'.</p>
"""


def _tabella(riepilogo: Dict[str, Any]) -> str:
    """Le altre grandezze, con nome esteso e unita'."""
    righe = []
    for chiave, etichetta, unita, decimali in ETICHETTE:
        if chiave not in riepilogo or chiave in CHIAVI_GUIDA:
            continue
        valore = _numero(riepilogo[chiave], decimali)
        righe.append(
            f"<tr><td>{_e(etichetta)}</td>"
            f'<td class="destra"><strong>{valore}</strong> '
            f'<span class="unita">{_e(unita)}</span></td></tr>'
        )
    if not righe:
        return ""
    return (
        '<table class="dati"><thead><tr><th>Grandezza</th>'
        '<th class="destra">Valore</th></tr></thead>'
        f'<tbody>{"".join(righe)}</tbody></table>'
    )


def _segnalazioni(anomalie: List[Dict[str, str]]) -> str:
    if not anomalie:
        return ""
    voci = "".join(
        f'<li class="{_e(a["livello"])}"><strong>{_e(a["contesto"])}</strong> '
        f"&ndash; {_e(a['messaggio'])}</li>"
        for a in anomalie
    )
    return f'<h2>Segnalazioni</h2><ul class="segnalazioni">{voci}</ul>'


FOGLIO = """
@page {
  size: A4;
  margin: 1.6cm 1.8cm 2.2cm;
  @bottom-left {
    content: "Comune di Imperia \\2013  TPL navette a guida autonoma";
    font-size: 7.5pt; color: #8a94a3;
  }
  @bottom-right {
    content: "pagina " counter(page) " di " counter(pages);
    font-size: 7.5pt; color: #8a94a3;
  }
}
body { font-family: "DejaVu Sans", sans-serif; font-size: 9.5pt; color: #1b2430; }

.testata { border-bottom: 3px solid #1f5f9e; padding-bottom: 10px; margin-bottom: 4px; }
.testata h1 { font-size: 17pt; margin: 0; color: #1f5f9e; letter-spacing: -.2px; }
.testata .ente { font-size: 8.5pt; color: #6b7789; text-transform: uppercase;
                 letter-spacing: 1.2px; margin-bottom: 3px; }
.identita { margin: 10px 0 18px; font-size: 9pt; color: #46505f; }
.identita span { display: inline-block; margin-right: 18px; }
.identita b { color: #1b2430; }

h2 { font-size: 11.5pt; color: #1f5f9e; margin: 20px 0 6px;
     border-bottom: 1px solid #d8e0ea; padding-bottom: 3px; }
h3 { font-size: 10pt; margin: 16px 0 4px; color: #46505f; }

.riquadri { display: flex; gap: 8px; margin: 12px 0 4px; }
.riquadro { flex: 1; background: #f4f7fb; border: 1px solid #d8e0ea;
            border-radius: 5px; padding: 9px 10px; }
.riquadro .valore { font-size: 15pt; font-weight: bold; color: #1f5f9e;
                    line-height: 1.1; }
.riquadro .unita { font-size: 8pt; font-weight: normal; color: #6b7789;
                   margin-left: 2px; }
.riquadro .etichetta { font-size: 7.5pt; color: #6b7789; margin-top: 2px;
                       text-transform: uppercase; letter-spacing: .4px; }

.barra { display: flex; height: 26px; border-radius: 4px; overflow: hidden;
         margin: 10px 0 4px; }
.parte { color: #fff; font-size: 8.5pt; font-weight: bold; text-align: center;
         line-height: 26px; }
.parte.autonoma { background: #1f5f9e; }
.parte.manuale { background: #9a6b00; }
.pallino { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
           margin-right: 6px; }
.pallino.autonoma { background: #1f5f9e; }
.pallino.manuale { background: #9a6b00; }

table.dati { border-collapse: collapse; width: 100%; margin-top: 8px;
             font-size: 9pt; }
table.dati th { background: #eef2f7; text-align: left; padding: 5px 8px;
                font-size: 8.5pt; color: #46505f; border-bottom: 1px solid #d8e0ea; }
table.dati td { padding: 4px 8px; border-bottom: 1px solid #e9eef4; }
table.dati tbody tr:nth-child(even) { background: #fafbfd; }
.destra { text-align: right; }
.unita { color: #6b7789; font-size: 8pt; }
.nota { font-size: 8pt; color: #6b7789; margin: 6px 0 0; line-height: 1.5; }

ul.segnalazioni { list-style: none; padding: 0; margin: 8px 0 0; font-size: 9pt; }
ul.segnalazioni li { border-left: 3px solid #9a6b00; background: #fdf9ef;
                     padding: 6px 10px; margin-bottom: 5px; border-radius: 0 4px 4px 0; }
ul.segnalazioni li.errore { border-left-color: #b3261e; background: #fdf0ee; }

.vuoto { color: #8a94a3; font-style: italic; }
.provenienza { margin-top: 22px; padding-top: 8px; border-top: 1px solid #d8e0ea;
               font-size: 7.5pt; color: #6b7789; line-height: 1.6; }
.provenienza code { font-family: "DejaVu Sans Mono", monospace; font-size: 7pt; }
"""


def componi(contesto: Dict[str, Any]) -> str:
    """HTML completo del report."""
    sezioni = []
    computer = contesto.get("computer") or {}

    # la telemetria per prima: e' la misura di bordo, non una derivata
    ordinati = sorted(
        computer.items(), key=lambda kv: kv[1].get("tipo") != "telemetria"
    )
    for pc, info in ordinati:
        riepilogo = info.get("riepilogo") or {}
        if not riepilogo:
            continue
        tipo = TIPI.get(info.get("tipo", ""), info.get("tipo", "?"))
        sezioni.append(f"<h2>{_e(tipo.capitalize())} <small>({_e(pc)})</small></h2>")
        sezioni.append(_in_evidenza(riepilogo))
        sezioni.append(_barra_guida(riepilogo))
        sezioni.append(_tabella(riepilogo))

    if not sezioni:
        sezioni.append('<p class="vuoto">Nessun dato analizzabile nell\'archivio.</p>')

    sezioni.append(_segnalazioni(contesto.get("anomalie") or []))

    return f"""<!DOCTYPE html><html lang="it"><head><meta charset="utf-8">
<style>{FOGLIO}</style></head><body>
<div class="testata">
  <div class="ente">Comune di Imperia &middot; Direzione Lavori TPL</div>
  <h1>Navetta {_e(contesto.get("navetta") or "-")}
      &ndash; giornata {_e(contesto.get("data_file") or "-")}</h1>
</div>
<div class="identita">
  <span>VIN <b>{_e(contesto.get("vin") or "-")}</b></span>
  <span>Elaborazione del <b>{_e(contesto.get("quando") or "-")}</b></span>
</div>
{"".join(sezioni)}
<div class="provenienza">
  Archivio originale: <code>{_e(contesto.get("originale", "-"))}</code><br>
  File elaborato: <code>{_e(contesto.get("elaborato") or "-")}</code><br>
  Archiviazione S3: <code>{_e(contesto.get("chiave_s3", "-"))}</code>
</div>
</body></html>"""
