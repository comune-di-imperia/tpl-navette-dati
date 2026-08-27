"""Sfondo cartografico per la pagina delle statistiche.

Le tessere si scaricano **una volta** e si compongono in una sola immagine,
servita poi dall'applicazione: il browser di chi consulta la pagina non
contatta alcun servizio esterno, e il Comune non manda gli indirizzi dei suoi
visitatori a terzi solo per mostrare una mappa.

La cartografia e' OpenStreetMap, licenza ODbL: l'attribuzione va mostrata
accanto alla mappa, ed e' scritta nel template.

Si rigenera con:
    python3 -m tpl_navette.mappa
e va rifatta soltanto se il percorso cambia.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

TESSERA = 256
SERVIZIO = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
CORTESIA = (
    "TPL Imperia - statistiche sperimentazione "
    "(comune.imperia.it)"
)

# Margine attorno ai punti, in gradi: senza, le fermate agli estremi
# finirebbero sul bordo dell'immagine.
MARGINE = 0.0016

# Margine attorno al percorso dopo il raddrizzamento, in pixel.
CORNICE = 78

# La mappa si inquadra sulle fermate, non sulle salite: il servizio arriva dove
# arrivano le fermate, anche dove non e' ancora salito nessuno.
from .fermate import FERMATE as CAPISALDI


def _percorso_uscita() -> Path:
    return Path(os.environ.get(
        "TPL_MAPPA_SFONDO",
        str(Path(__file__).parent / "static" / "percorso.png")))


def _in_tessere(lat: float, lon: float, zoom: int) -> Tuple[float, float]:
    """Coordinate in tessere, con la parte decimale: serve per posizionare."""
    n = 2.0 ** zoom
    x = (lon + 180.0) / 360.0 * n
    rad = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(rad)) / math.pi) / 2.0 * n
    return x, y


def riquadro(punti: List[Dict]) -> Tuple[float, float, float, float]:
    """Estremi che contengono tutti i punti, con un margine."""
    lat = [p["lat"] for p in punti]
    lon = [p["lon"] for p in punti]
    return (min(lat) - MARGINE, min(lon) - MARGINE * 1.4,
            max(lat) + MARGINE, max(lon) + MARGINE * 1.4)


def scegli_zoom(estremi: Tuple[float, float, float, float],
                larghezza: int, altezza: int) -> int:
    """Il massimo ingrandimento che fa stare tutto dentro l'immagine."""
    lat_min, lon_min, lat_max, lon_max = estremi
    for zoom in range(19, 10, -1):
        x0, y0 = _in_tessere(lat_max, lon_min, zoom)
        x1, y1 = _in_tessere(lat_min, lon_max, zoom)
        if ((x1 - x0) * TESSERA <= larghezza
                and (y1 - y0) * TESSERA <= altezza):
            return zoom
    return 13


def _inclinazione(punti: List[Dict]) -> float:
    """Di quanto pende la linea di trasporto, in gradi.

    Il percorso di Imperia corre lungo la costa da sud-ovest a nord-est, a una
    quarantina di gradi: lasciato dritto occupa un quadrato, raddrizzato sta in
    una striscia alta la meta'. Si misura fra i due punti piu' lontani fra
    loro, che sono i capi del percorso.
    """
    if len(punti) < 2:
        return 0.0

    lontani = max(
        ((a, b) for i, a in enumerate(punti) for b in punti[i + 1:]),
        key=lambda coppia: ((coppia[0]["lat"] - coppia[1]["lat"]) ** 2
                            + (coppia[0]["lon"] - coppia[1]["lon"]) ** 2))
    a, b = lontani
    if a["lon"] > b["lon"]:
        a, b = b, a

    # In metri: alle nostre latitudini un grado di longitudine vale meno di
    # uno di latitudine, e senza la correzione l'angolo verrebbe sbagliato.
    verso_est = (b["lon"] - a["lon"]) * math.cos(math.radians(a["lat"]))
    verso_nord = b["lat"] - a["lat"]
    return math.degrees(math.atan2(verso_nord, verso_est))


def componi(punti: List[Dict], larghezza: int = 1100,
            raddrizza: bool = True) -> Dict:
    """Scarica le tessere, le incolla e raddrizza il percorso.

    L'immagine viene ruotata finche' la linea di trasporto non e' quasi
    orizzontale, poi tagliata attorno ai punti: cosi' la mappa e' una striscia
    invece di un quadrato mezzo vuoto. Il nord non e' piu' in alto, e la
    pagina lo dice.

    Il valore restituito dice, per ogni punto, dove disegnarlo in pixel: il
    template ci mette sopra i cerchi, senza bisogno di una libreria di mappe.
    """
    from io import BytesIO

    import requests
    from PIL import Image

    # Il riquadro comprende anche i capisaldi, che pero' non sono punti di
    # salita e non vanno disegnati: entrano solo nel calcolo dell'inquadratura.
    estremi = riquadro(list(punti) + [dict(c) for c in CAPISALDI])
    lat_min, lon_min, lat_max, lon_max = estremi

    zoom = 19
    while zoom > 10:
        x0, _ = _in_tessere(lat_max, lon_min, zoom)
        x1, _ = _in_tessere(lat_min, lon_max, zoom)
        if (x1 - x0) * TESSERA <= larghezza * 1.6:
            break
        zoom -= 1

    x0, y0 = _in_tessere(lat_max, lon_min, zoom)
    x1, y1 = _in_tessere(lat_min, lon_max, zoom)

    prima_x = int(math.floor(x0))
    prima_y = int(math.floor(y0))
    quante_x = int(math.ceil(x1)) - prima_x
    quante_y = int(math.ceil(y1)) - prima_y

    tela = Image.new("RGB", (quante_x * TESSERA, quante_y * TESSERA), "#e8ecf1")
    sessione = requests.Session()
    sessione.headers["User-Agent"] = CORTESIA

    for dx in range(quante_x):
        for dy in range(quante_y):
            risposta = sessione.get(
                SERVIZIO.format(z=zoom, x=prima_x + dx, y=prima_y + dy),
                timeout=30)
            if not risposta.ok:
                continue
            tessera = Image.open(BytesIO(risposta.content)).convert("RGB")
            tela.paste(tessera, (dx * TESSERA, dy * TESSERA))

    # Posizione dei punti sulla tela intera, prima di ruotare.
    def sulla_tela(punto):
        px, py = _in_tessere(punto["lat"], punto["lon"], zoom)
        return ((px - prima_x) * TESSERA, (py - prima_y) * TESSERA)

    # L'inclinazione si misura sui capi del percorso, capisaldi compresi:
    # altrimenti il tratto oltre l'ultima salita resterebbe storto.
    angolo = (_inclinazione(list(punti) + [dict(c) for c in CAPISALDI])
              if raddrizza else 0.0)

    if angolo:
        # Pillow ruota in senso antiorario: per portare a orizzontale una
        # linea che sale verso destra bisogna girare dello stesso angolo.
        ruotata = tela.rotate(-angolo, resample=Image.BICUBIC, expand=True,
                              fillcolor="#e8ecf1")
    else:
        ruotata = tela

    cx, cy = tela.width / 2.0, tela.height / 2.0
    ncx, ncy = ruotata.width / 2.0, ruotata.height / 2.0
    seno = math.sin(math.radians(-angolo))
    coseno = math.cos(math.radians(-angolo))

    def dopo_rotazione(x, y):
        dx, dy = x - cx, y - cy
        return (coseno * dx + seno * dy + ncx,
                -seno * dx + coseno * dy + ncy)

    posizioni = [dopo_rotazione(*sulla_tela(p))
                 for p in list(punti) + [dict(c) for c in CAPISALDI]]
    sx = max(0, int(min(p[0] for p in posizioni)) - CORNICE)
    sy = max(0, int(min(p[1] for p in posizioni)) - CORNICE)
    dx_ = min(ruotata.width, int(max(p[0] for p in posizioni)) + CORNICE)
    dy_ = min(ruotata.height, int(max(p[1] for p in posizioni)) + CORNICE)
    tagliata = ruotata.crop((sx, sy, dx_, dy_))

    uscita = _percorso_uscita()
    uscita.parent.mkdir(parents=True, exist_ok=True)
    tagliata.save(uscita, optimize=True)

    riferimento = {
        "file": uscita.name,
        "larghezza": tagliata.width,
        "altezza": tagliata.height,
        "zoom": zoom,
        "angolo": round(angolo, 2),
        "prima_tessera": [prima_x, prima_y],
        "centro": [cx, cy],
        "nuovo_centro": [ncx, ncy],
        "taglio": [sx, sy],
    }
    uscita.with_suffix(".json").write_text(
        json.dumps(riferimento, indent=2), encoding="utf-8")

    riferimento["punti"] = colloca(punti, riferimento)
    riferimento["capisaldi"] = colloca(
        [{**c, "quante": 0} for c in CAPISALDI], riferimento)
    return riferimento


def leggi_riferimento() -> Optional[Dict]:
    """Parametri dello sfondo gia' composto, se c'e'."""
    descrizione = _percorso_uscita().with_suffix(".json")
    if not descrizione.exists() or not _percorso_uscita().exists():
        return None
    try:
        return json.loads(descrizione.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def colloca(punti: List[Dict], riferimento: Dict) -> List[Dict]:
    """Posiziona i punti sullo sfondo, ripetendone le trasformazioni.

    Si rifanno gli stessi passaggi con cui l'immagine e' stata composta:
    proiezione, rotazione attorno al centro, taglio. Un punto che finisce
    fuori significa che il servizio si e' esteso oltre il tratto per cui la
    mappa era stata fatta: si segnala invece di disegnarlo sul bordo, dove
    direbbe una cosa falsa.
    """
    zoom = riferimento["zoom"]
    prima_x, prima_y = riferimento["prima_tessera"]
    cx, cy = riferimento["centro"]
    ncx, ncy = riferimento["nuovo_centro"]
    sx, sy = riferimento["taglio"]
    angolo = riferimento.get("angolo", 0.0)
    seno = math.sin(math.radians(-angolo))
    coseno = math.cos(math.radians(-angolo))

    dentro, fuori = [], 0
    for punto in punti:
        px, py = _in_tessere(punto["lat"], punto["lon"], zoom)
        x = (px - prima_x) * TESSERA - cx
        y = (py - prima_y) * TESSERA - cy
        x, y = coseno * x + seno * y + ncx, -seno * x + coseno * y + ncy
        x, y = x - sx, y - sy
        if not (0 <= x <= riferimento["larghezza"]
                and 0 <= y <= riferimento["altezza"]):
            fuori += 1
            continue
        dentro.append({**punto, "x": round(x, 1), "y": round(y, 1)})
    riferimento["fuori_mappa"] = fuori
    return dentro


if __name__ == "__main__":
    import json
    import sys

    dati = json.loads(sys.stdin.read())
    print(json.dumps(componi(dati), indent=2))
