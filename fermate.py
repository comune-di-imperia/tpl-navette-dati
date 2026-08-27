"""Le fermate della sperimentazione, con le loro coordinate.

L'applicazione di bordo non registra quale fermata sia stata inquadrata: della
salita resta solo la posizione del mezzo. Le fermate stanno quindi qui, e ogni
salita viene attribuita alla piu' vicina.

Le coordinate le ha fornite la Direzione Lavori. Se una fermata si sposta o se
ne aggiunge una, si modifica questo elenco e si rigenera la mappa:
    python3 -m tpl_navette.mappa
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

# Oltre questa distanza dalla fermata piu' vicina la salita non le viene
# attribuita: e' successo altrove, e dirlo e' piu' onesto che forzarla dentro.
#
# Cento metri e non cinquanta: la posizione registrata e' quella del **mezzo**
# alla salita, e un veicolo che ha gia' accostato o sta ripartendo mentre
# l'operatore inquadra il codice si trova a qualche decina di metri dal palo.
# Sui dati di agosto le salite al Municipio cadono a 63 metri e quella di Via
# Trento a 51: con la soglia a cinquanta sparirebbero entrambe.
RAGGIO_M = 100.0

# `tecnico` distingue i luoghi di servizio dalle fermate per i passeggeri:
# all'hub di sosta e ricarica si sale, ma non e' utenza del trasporto, e
# contarlo insieme alle altre gonfierebbe i numeri della sperimentazione.
FERMATE: Tuple[Dict, ...] = (
    {"nome": "Capolinea Porto Maurizio",
     "lat": 43.880500, "lon": 8.019241},
    {"nome": "Piscina",
     "lat": 43.882131, "lon": 8.021924},
    # Corretta sul punto dove le salite si addensano davvero: la coordinata
    # fornita in origine cadeva 63 metri piu' a sud-ovest.
    {"nome": "Municipio",
     "lat": 43.884738, "lon": 8.027217},
    {"nome": "Via Trento",
     "lat": 43.890732, "lon": 8.033991},
    {"nome": "Capolinea Oneglia",
     "lat": 43.892337, "lon": 8.038089},
    {"nome": "Hub di sosta e ricarica",
     "lat": 43.879818, "lon": 8.017978, "tecnico": True},
)


def distanza_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distanza in metri, con la formula dell'emisenoverso."""
    raggio = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * raggio * math.asin(math.sqrt(a))


def piu_vicina(lat: float, lon: float) -> Optional[Dict]:
    """La fermata piu' vicina, se sta entro il raggio ammesso."""
    if not FERMATE:
        return None
    vicina = min(FERMATE,
                 key=lambda f: distanza_m(lat, lon, f["lat"], f["lon"]))
    if distanza_m(lat, lon, vicina["lat"], vicina["lon"]) > RAGGIO_M:
        return None
    return vicina


def conta_salite(posizioni: List[Tuple]) -> Dict:
    """Salite attribuite a ciascun punto, distinguendo servizio e tecnica.

    Le salite lontane da ogni punto non spariscono: si contano a parte, e la
    pagina lo dice. Sono il segnale che l'elenco e' incompleto o che qualcuno
    e' salito dove non doveva.
    """
    conteggio = {f["nome"]: 0 for f in FERMATE}
    lontane = []
    for lat, lon in posizioni:
        if lat is None or lon is None:
            continue
        lat, lon = float(lat), float(lon)
        fermata = piu_vicina(lat, lon)
        if fermata is None:
            lontane.append({"lat": round(lat, 6), "lon": round(lon, 6)})
            continue
        conteggio[fermata["nome"]] += 1

    elenco = [{**f, "quante": conteggio[f["nome"]]} for f in FERMATE]
    return {
        "fermate": [f for f in elenco if not f.get("tecnico")],
        "tecnici": [f for f in elenco if f.get("tecnico")],
        "lontane": lontane,
    }
