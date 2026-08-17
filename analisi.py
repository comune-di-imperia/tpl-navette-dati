"""Lettura e analisi dei file giornalieri delle navette a guida autonoma.

Il file caricato dall'operatore e' uno ZIP prodotto dall'estrattore dati Navya:

    <navetta>_<data>_<VIN>_<data>_<ora>.zip
      └── ..._pc1.tar.gz  →  data-extractor_<data>_<ora>.h5
      └── ..._pc2.tar.gz  →  data-extractor_<data>_<ora>.h5

Ogni ``.h5`` e' un HDFStore pandas con un gruppo per VIN e, sotto, una tabella
per segnale (``timestamp``, ``timeOfIssue``, ``value``) campionata a 10 Hz.

I segnali presenti descrivono posizione e assetto, non la cinematica: da qui i
tre parametri calcolati (velocita', accelerazione, distanza percorsa).
"""

from __future__ import annotations

import gzip
import math
import os
import re
import tarfile
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# I due computer di bordo esportano insiemi di segnali DIVERSI:
#   pc1 -> telemetria del veicolo (CAN): velocita', odometro, batteria, porte,
#          modalita' di guida, temperature, luci, sterzo
#   pc2 -> localizzazione e navigazione: posizione, assetto, coordinate, qualita'
# Quale sia l'uno o l'altro non e' garantito dal nome: si riconosce dai segnali.
SEGNALI_POSIZIONE = ("X_Fusion", "Y_Fusion", "Z_Fusion")
SEGNALI_ASSETTO = ("RX_Fusion", "RY_Fusion", "RZ_Fusion")
SEGNALI_GEO = ("latitude", "longitude", "altitude")
SEGNALI_QUALITA = ("Hit_Ratio", "GNSS_Corrections_Age")
SEGNALI_LOCALIZZAZIONE = (
    SEGNALI_POSIZIONE + SEGNALI_ASSETTO + SEGNALI_GEO + SEGNALI_QUALITA
)
SEGNALI_TELEMETRIA_CHIAVE = (
    "Vehicle_Speed",
    "Mileage",
    "Battery_Level",
    "Robot_Mode",
    "Vehicle_Mode",
    "Doors_Status",
)


def tipo_computer(segnali: Dict[str, Any]) -> str:
    """Riconosce il ruolo del file dai segnali presenti."""
    if "X_Fusion" in segnali and "Y_Fusion" in segnali:
        return "localizzazione"
    if "Vehicle_Speed" in segnali or "Mileage" in segnali:
        return "telemetria"
    return "sconosciuto"


# I tre parametri che l'estrattore non fornisce e che vengono aggiunti qui.
# Nessuno dei tre e' esportato dall'estrattore Navya:
#   accelerazione  -> derivata dalla velocita' misurata a bordo (Vehicle_Speed)
#   giri motore    -> derivati dalla velocita' e dalla geometria della ruota
#   pitch          -> dall'assetto RY_Fusion del computer di localizzazione
PARAMETRI_CALCOLATI = ("accelerazione_mps2", "giri_motore_rpm", "pitch_gradi")

# Grandezze di appoggio, calcolate perche' servono ai tre parametri sopra e
# perche' danno la controprova sui totali di percorrenza.
PARAMETRI_APPOGGIO = ("velocita_kmh", "distanza_m")

# Geometria della ruota della navetta (pneumatico 205/55 R16):
#   raggio = 16" / 2 * 25,4 + 205 * 0,55 = 203,2 + 112,75 = 315,95 mm
# Sovrascrivibile con TPL_RAGGIO_RUOTA_M se Navya fornisce il dato esatto.
RAGGIO_RUOTA_M = float(os.environ.get("TPL_RAGGIO_RUOTA_M", "0.316"))

# Rapporto di riduzione fra motore e ruota. NON e' noto dalla documentazione
# in nostro possesso: finche' vale 1 il valore prodotto e' il regime della
# RUOTA, non del motore, e il riepilogo lo dichiara esplicitamente.
RAPPORTO_TRASMISSIONE = float(os.environ.get("TPL_RAPPORTO_TRASMISSIONE", "1.0"))

# Assetto: verificato empiricamente sui dati reali di BG08 correlando ciascun
# angolo con la pendenza del percorso ricavata da X/Y/Z_Fusion.
# RY -0,68 (e' il beccheggio), RX -0,01 (rollio), RZ copre l'intero giro (imbardata).
ANGOLI_ASSETTO = {
    "RY_Fusion": "pitch_gradi",  # beccheggio: e' questo il parametro richiesto
    "RX_Fusion": "rollio_gradi",
    "RZ_Fusion": "imbardata_gradi",
}

# Oltre questo intervallo fra due campioni non si tratta di continuita' di
# marcia ma di una nuova sessione: la velocita' non va calcolata a cavallo.
SOGLIA_NUOVA_SESSIONE_S = 60.0

# Velocita' oltre la quale il campione e' considerato un artefatto di
# localizzazione: una navetta di questo tipo non supera i 25 km/h.
VELOCITA_MAX_PLAUSIBILE_KMH = 40.0

RE_NOME = re.compile(
    r"^(?P<navetta>[A-Z0-9]+)_(?P<data>\d{8})_(?P<vin>[A-Z0-9]+)_"
    r"(?P<data2>\d{8})_(?P<ora>\d{2}h\d{2}m\d{2}s)"
)


class ArchivioNonValido(ValueError):
    """Lo ZIP non e' leggibile o non ha la struttura attesa."""


@dataclass
class Anomalia:
    """Problema rilevato che non impedisce di proseguire sul resto."""

    livello: str  # "avviso" | "errore"
    contesto: str
    messaggio: str


@dataclass
class Esito:
    """Risultato dell'analisi di un archivio."""

    navetta: str = ""
    vin: str = ""
    data_file: str = ""
    computer: Dict[str, Any] = field(default_factory=dict)
    anomalie: List[Anomalia] = field(default_factory=list)

    @property
    def ha_errori(self) -> bool:
        return any(a.livello == "errore" for a in self.anomalie)

    def come_dizionario(self) -> Dict[str, Any]:
        return {
            "navetta": self.navetta,
            "vin": self.vin,
            "data_file": self.data_file,
            "computer": self.computer,
            "anomalie": [
                {"livello": a.livello, "contesto": a.contesto, "messaggio": a.messaggio}
                for a in self.anomalie
            ],
        }


def metadati_da_nome(nome: str) -> Dict[str, str]:
    """Ricava navetta, VIN, data e ora dal nome del file."""
    m = RE_NOME.match(Path(nome).stem)
    if not m:
        return {}
    d = m.groupdict()
    return {
        "navetta": d["navetta"],
        "vin": d["vin"],
        "data": f"{d['data'][:4]}-{d['data'][4:6]}-{d['data'][6:]}",
        "ora": d["ora"].replace("h", ":").replace("m", ":").replace("s", ""),
    }


def estrai_archivio(zip_path: Path, destinazione: Path) -> Dict[str, Path]:
    """Estrae lo ZIP e i tar.gz interni. Ritorna {computer: percorso .h5}.

    Gli archivi corrotti non interrompono l'elaborazione: vengono saltati e
    segnalati dal chiamante confrontando le chiavi attese con quelle ottenute.
    """
    destinazione.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            danneggiato = z.testzip()
            if danneggiato:
                raise ArchivioNonValido(f"voce ZIP corrotta: {danneggiato}")
            z.extractall(destinazione)
    except zipfile.BadZipFile as e:
        raise ArchivioNonValido(f"file ZIP illeggibile: {e}") from e

    trovati: Dict[str, Path] = {}
    for tgz in sorted(destinazione.rglob("*_pc[0-9].tar.gz")):
        pc = re.search(r"_(pc\d)\.tar\.gz$", tgz.name).group(1)
        fuori = destinazione / pc
        fuori.mkdir(exist_ok=True)
        try:
            with tarfile.open(tgz, "r:gz") as t:
                t.extractall(fuori)
        except (tarfile.TarError, EOFError, OSError):
            # archivio troncato: capitato davvero sul pc1 di BF05 del 03/08
            continue
        h5 = next(iter(fuori.rglob("*.h5")), None)
        if h5:
            trovati[pc] = h5
    return trovati


def _leggi_segnali(h5: Path) -> tuple[str, Dict[str, pd.DataFrame]]:
    """Legge tutte le tabelle del file. Ritorna (vin, {segnale: dataframe})."""
    segnali: Dict[str, pd.DataFrame] = {}
    vin = ""
    with pd.HDFStore(str(h5), mode="r") as store:
        for chiave in store.keys():
            parti = [p for p in chiave.split("/") if p]
            if len(parti) != 2:
                continue
            vin, nome = parti
            df = store.select(chiave)
            if "timestamp" not in df.columns or "value" not in df.columns:
                continue
            df = df[["timestamp", "value"]].copy()
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            segnali[nome] = df.sort_values("timestamp").reset_index(drop=True)
    return vin, segnali


def calcola_parametri(segnali: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Aggiunge i tre parametri assenti nel file dell'estrattore.

    L'estrattore fornisce posizione (``X_Fusion``/``Y_Fusion``, metri nel
    sistema locale) e assetto, ma nessuna grandezza cinematica. Si derivano:

    - ``velocita_kmh``      modulo della velocita' sul piano
    - ``accelerazione_mps2`` derivata della velocita'
    - ``distanza_m``        distanza cumulata dall'inizio della sessione

    I campioni a cavallo di una pausa (oltre ``SOGLIA_NUOVA_SESSIONE_S``) non
    generano velocita': altrimenti lo spostamento fra due sessioni verrebbe
    diviso per un tempo enorme, oppure - peggio - un riavvio in un punto
    diverso produrrebbe un picco inventato.
    """
    if tipo_computer(segnali) != "localizzazione":
        raise ArchivioNonValido(
            "i parametri cinematici si calcolano dal file di localizzazione "
            "(X_Fusion/Y_Fusion), assente in questo computer di bordo"
        )

    x = segnali["X_Fusion"].rename(columns={"value": "x"})
    y = segnali["Y_Fusion"].rename(columns={"value": "y"})
    df = pd.merge_asof(
        x, y, on="timestamp", direction="nearest", tolerance=pd.Timedelta("50ms")
    ).dropna(subset=["x", "y"])

    dt = df["timestamp"].diff().dt.total_seconds()
    dx = df["x"].diff()
    dy = df["y"].diff()
    passo = (dx**2 + dy**2) ** 0.5

    nuova_sessione = (dt > SOGLIA_NUOVA_SESSIONE_S) | dt.isna()
    df["sessione"] = nuova_sessione.cumsum()

    passo = passo.mask(nuova_sessione, other=0.0)
    velocita_mps = (passo / dt).mask(nuova_sessione, other=0.0).fillna(0.0)
    velocita_kmh = velocita_mps * 3.6

    # I salti di localizzazione producono velocita' impossibili: si annullano
    # sia in velocita' sia nella distanza, per non gonfiare i totali.
    implausibile = velocita_kmh > VELOCITA_MAX_PLAUSIBILE_KMH
    velocita_kmh = velocita_kmh.mask(implausibile, other=float("nan"))
    passo = passo.mask(implausibile, other=0.0)

    df["velocita_kmh"] = velocita_kmh
    df["accelerazione_mps2"] = (
        (velocita_kmh / 3.6).diff().div(dt).mask(nuova_sessione, other=0.0)
    )
    df["distanza_m"] = passo.groupby(df["sessione"]).cumsum()
    df["scarto_localizzazione"] = implausibile
    return _aggiungi_assetto(df, segnali)


def calcola_parametri_telemetria(segnali: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Accelerazione e regime di rotazione dalla velocita' misurata a bordo.

    ``Vehicle_Speed`` e' in m/s a 10 Hz: e' la fonte migliore per l'accelerazione,
    meglio della derivata seconda della posizione, che amplifica il rumore del
    posizionamento. I giri seguono dalla geometria della ruota:

        giri/min = v / (2 * pi * raggio) * 60 * rapporto

    Con ``RAPPORTO_TRASMISSIONE`` a 1 il risultato e' il regime della ruota.
    """
    if "Vehicle_Speed" not in segnali:
        raise ArchivioNonValido(
            "accelerazione e giri richiedono Vehicle_Speed, assente in questo file"
        )

    df = segnali["Vehicle_Speed"].rename(columns={"value": "velocita_mps"}).copy()
    df["velocita_mps"] = pd.to_numeric(df["velocita_mps"], errors="coerce")
    df = df.dropna(subset=["velocita_mps"])

    dt = df["timestamp"].diff().dt.total_seconds()
    nuova_sessione = (dt > SOGLIA_NUOVA_SESSIONE_S) | dt.isna()
    df["sessione"] = nuova_sessione.cumsum()

    df["velocita_kmh"] = df["velocita_mps"] * 3.6
    df["accelerazione_mps2"] = (
        df["velocita_mps"].diff().div(dt).mask(nuova_sessione, other=0.0)
    )

    giri_ruota = df["velocita_mps"] / (2 * math.pi * RAGGIO_RUOTA_M) * 60.0
    df["giri_ruota_rpm"] = giri_ruota
    df["giri_motore_rpm"] = giri_ruota * RAPPORTO_TRASMISSIONE

    # distanza per omogeneita' con il file di localizzazione
    df["distanza_m"] = (
        (df["velocita_mps"] * dt)
        .mask(nuova_sessione, other=0.0)
        .fillna(0.0)
        .groupby(df["sessione"])
        .cumsum()
    )
    return df


def _aggiungi_assetto(
    df: pd.DataFrame, segnali: Dict[str, pd.DataFrame]
) -> pd.DataFrame:
    """Porta gli angoli di assetto sulla stessa base tempi, convertiti in gradi.

    L'estrattore li fornisce in radianti e con nomi che non dicono quale sia
    quale: la corrispondenza in ``ANGOLI_ASSETTO`` e' stata verificata sui dati.
    """
    for segnale, colonna in ANGOLI_ASSETTO.items():
        if segnale not in segnali:
            continue
        a = segnali[segnale].rename(columns={"value": colonna}).copy()
        a[colonna] = pd.to_numeric(a[colonna], errors="coerce") * 180.0 / math.pi
        df = pd.merge_asof(
            df, a, on="timestamp", direction="nearest", tolerance=pd.Timedelta("50ms")
        )
    return df


def riepilogo(df: pd.DataFrame, segnali: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    """Sintesi leggibile dell'elaborazione, usata nel PDF e nella dashboard."""
    v = df["velocita_kmh"].dropna()
    in_moto = v[v > 0.5]
    durata = (df["timestamp"].max() - df["timestamp"].min()).total_seconds()
    sessioni = int(df["sessione"].nunique())

    ris: Dict[str, Any] = {
        "campioni": len(df),
        "sessioni": sessioni,
        "dal": str(df["timestamp"].min()),
        "al": str(df["timestamp"].max()),
        "durata_ore": round(durata / 3600, 2) if durata else 0.0,
        "distanza_totale_km": round(
            float(df.groupby("sessione")["distanza_m"].max().sum()) / 1000, 3
        ),
        "velocita_media_kmh": round(float(in_moto.mean()), 2) if len(in_moto) else 0.0,
        "velocita_massima_kmh": round(float(v.max()), 2) if len(v) else 0.0,
        "tempo_in_moto_ore": round(float(len(in_moto)) * 0.1 / 3600, 2),
        "campioni_scartati": int(df["scarto_localizzazione"].sum()),
    }
    if "Hit_Ratio" in segnali:
        hr = segnali["Hit_Ratio"]["value"].astype(float)
        ris["hit_ratio_medio"] = round(float(hr.mean()), 4)
        ris["hit_ratio_minimo"] = round(float(hr.min()), 4)
    if "GNSS_Corrections_Age" in segnali:
        ga = segnali["GNSS_Corrections_Age"]["value"].astype(float)
        ris["eta_correzioni_gnss_media_s"] = round(float(ga.mean()), 3)
        ris["eta_correzioni_gnss_max_s"] = round(float(ga.max()), 1)
    if "latitude" in segnali and "longitude" in segnali:
        ris["latitudine_media"] = round(float(segnali["latitude"]["value"].mean()), 6)
        ris["longitudine_media"] = round(float(segnali["longitude"]["value"].mean()), 6)

    # assetto: interessa solo mentre il veicolo si muove, da fermo il pitch
    # descrive la pendenza del posteggio e falserebbe le medie
    moto = df["velocita_kmh"] > 0.5
    for colonna, etichetta in (
        ("pitch_gradi", "pitch_beccheggio"),
        ("rollio_gradi", "rollio"),
    ):
        if colonna not in df.columns:
            continue
        s = df.loc[moto, colonna].dropna()
        if not len(s):
            continue
        ris[f"{etichetta}_medio_gradi"] = round(float(s.mean()), 2)
        ris[f"{etichetta}_minimo_gradi"] = round(float(s.min()), 2)
        ris[f"{etichetta}_massimo_gradi"] = round(float(s.max()), 2)
    return ris


def riepilogo_parametri_telemetria(df: pd.DataFrame) -> Dict[str, Any]:
    """Sintesi dei parametri derivati dalla telemetria."""
    ris: Dict[str, Any] = {}
    moto = df["velocita_mps"] > 0.15

    a = df.loc[moto, "accelerazione_mps2"].dropna()
    if len(a):
        ris["accelerazione_massima_mps2"] = round(float(a.max()), 3)
        ris["decelerazione_massima_mps2"] = round(float(a.min()), 3)
        ris["accelerazione_media_mps2"] = round(float(a[a > 0].mean()), 3)
        ris["decelerazione_media_mps2"] = round(float(a[a < 0].mean()), 3)

    g = df.loc[moto, "giri_motore_rpm"].dropna()
    if len(g):
        ris["giri_medi_rpm"] = round(float(g.mean()), 1)
        ris["giri_massimi_rpm"] = round(float(g.max()), 1)
        ris["giri_riferiti_a"] = (
            "ruota (rapporto di trasmissione non noto)"
            if RAPPORTO_TRASMISSIONE == 1.0
            else f"motore (rapporto {RAPPORTO_TRASMISSIONE})"
        )
        ris["raggio_ruota_m"] = RAGGIO_RUOTA_M
    return ris


def riepilogo_telemetria(segnali: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    """Sintesi del file di telemetria (pc con i dati CAN del veicolo).

    Qui velocita' e odometro sono misurati a bordo, non derivati: quando questo
    file e' disponibile e' la fonte da preferire per i totali di percorrenza.
    """
    ris: Dict[str, Any] = {}

    def _serie(nome: str) -> Optional[pd.Series]:
        if nome not in segnali:
            return None
        return pd.to_numeric(segnali[nome]["value"], errors="coerce").dropna()

    # Vehicle_Speed e' in m/s: confrontato con la velocita' derivata dalla
    # posizione (pc2) i valori coincidono solo dopo la conversione.
    v = _serie("Vehicle_Speed")
    if v is not None and len(v):
        v_kmh = v * 3.6
        in_moto = v_kmh[v_kmh > 0.5]
        ris["velocita_massima_kmh"] = round(float(v_kmh.max()), 2)
        ris["velocita_media_kmh"] = (
            round(float(in_moto.mean()), 2) if len(in_moto) else 0.0
        )
    # Mileage e' un odometro cumulativo in METRI (verificato: la differenza
    # coincide con la distanza calcolata dalla posizione entro l'1,5%).
    km = _serie("Mileage")
    if km is not None and len(km):
        ris["odometro_iniziale_km"] = round(float(km.min()) / 1000, 3)
        ris["odometro_finale_km"] = round(float(km.max()) / 1000, 3)
        ris["percorrenza_odometro_km"] = round(float(km.max() - km.min()) / 1000, 3)
    bat = _serie("Battery_Level")
    if bat is not None and len(bat):
        ris["batteria_iniziale_pct"] = round(float(bat.iloc[0]), 1)
        ris["batteria_finale_pct"] = round(float(bat.iloc[-1]), 1)
        ris["batteria_minima_pct"] = round(float(bat.min()), 1)
    for nome, etichetta in (
        ("Temperature_Cabin_Deg", "temperatura_cabina"),
        ("Temperature_Outside_Deg", "temperatura_esterna"),
        ("Temperature_Engine_Deg", "temperatura_motore"),
    ):
        s = _serie(nome)
        if s is not None and len(s):
            ris[f"{etichetta}_media"] = round(float(s.mean()), 1)
            ris[f"{etichetta}_max"] = round(float(s.max()), 1)
    porte = _serie("Doors_Status")
    if porte is not None and len(porte):
        # ogni transizione 0->diverso da 0 e' un'apertura
        aperture = int(((porte.shift(1).fillna(0) == 0) & (porte != 0)).sum())
        ris["aperture_porte"] = aperture
    for nome, etichetta in (
        ("Robot_Mode", "modalita_robot"),
        ("Vehicle_Mode", "modalita_veicolo"),
    ):
        if nome in segnali:
            conteggi = segnali[nome]["value"].value_counts()
            tot = int(conteggi.sum())
            ris[etichetta] = {
                str(k): round(100.0 * int(c) / tot, 1) for k, c in conteggi.items()
            }
    return ris


def analizza_archivio(zip_path: Path, lavoro: Path) -> Esito:
    """Analizza un archivio giornaliero completo (entrambi i computer)."""
    esito = Esito(
        **{
            k: v
            for k, v in metadati_da_nome(zip_path.name).items()
            if k in ("navetta", "vin")
        }
    )
    meta = metadati_da_nome(zip_path.name)
    esito.data_file = meta.get("data", "")
    if not meta:
        esito.anomalie.append(
            Anomalia(
                "avviso", zip_path.name, "nome file non conforme allo schema atteso"
            )
        )

    trovati = estrai_archivio(zip_path, lavoro)
    if not trovati:
        raise ArchivioNonValido(
            "nessun archivio interno leggibile: entrambi i computer di bordo "
            "hanno prodotto file danneggiati"
        )

    with zipfile.ZipFile(zip_path) as z:
        attesi = {
            re.search(r"_(pc\d)\.tar\.gz$", n).group(1)
            for n in z.namelist()
            if re.search(r"_pc\d\.tar\.gz$", n)
        }
    for pc in sorted(attesi - set(trovati)):
        esito.anomalie.append(
            Anomalia(
                "errore",
                pc,
                "archivio danneggiato o troncato: i dati di questo computer di "
                "bordo non sono recuperabili",
            )
        )

    for pc, h5 in sorted(trovati.items()):
        vin, segnali = _leggi_segnali(h5)
        if vin and not esito.vin:
            esito.vin = vin
        tipo = tipo_computer(segnali)
        voce: Dict[str, Any] = {
            "file": h5.name,
            "tipo": tipo,
            "segnali": sorted(segnali),
        }

        if tipo == "localizzazione":
            assenti = [s for s in SEGNALI_LOCALIZZAZIONE if s not in segnali]
            if assenti:
                esito.anomalie.append(
                    Anomalia("avviso", pc, "segnali assenti: " + ", ".join(assenti))
                )
            df = calcola_parametri(segnali)
            voce["riepilogo"] = riepilogo(df, segnali)
            voce["_dati"] = df
        elif tipo == "telemetria":
            assenti = [s for s in SEGNALI_TELEMETRIA_CHIAVE if s not in segnali]
            if assenti:
                esito.anomalie.append(
                    Anomalia("avviso", pc, "segnali assenti: " + ", ".join(assenti))
                )
            voce["riepilogo"] = riepilogo_telemetria(segnali)
            try:
                df = calcola_parametri_telemetria(segnali)
            except ArchivioNonValido as e:
                esito.anomalie.append(Anomalia("avviso", pc, str(e)))
            else:
                voce["riepilogo"].update(riepilogo_parametri_telemetria(df))
                voce["_dati"] = df
        else:
            esito.anomalie.append(
                Anomalia(
                    "avviso",
                    pc,
                    "insieme di segnali non riconosciuto: ne' localizzazione "
                    "ne' telemetria",
                )
            )
            voce["riepilogo"] = {}

        esito.computer[pc] = voce
    return esito


def scrivi_elaborato(esito: Esito, destinazione: Path) -> Path:
    """Scrive il file elaborato: originale + i tre parametri aggiunti.

    Si mantiene HDFStore, cosi' il formato resta quello che il committente
    riceve gia' dall'estrattore, con in piu' le colonne calcolate.
    """
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    with pd.HDFStore(str(destinazione), mode="w", complevel=5, complib="blosc") as out:
        scritti = 0
        for pc, info in esito.computer.items():
            df = info.get("_dati")
            if df is None:
                continue
            colonne = [c for c in df.columns if not c.startswith("_")]
            gruppo = f"/{pc}/{'cinematica' if info['tipo'] == 'localizzazione' else 'telemetria'}"
            out.put(gruppo, df[colonne], format="table")
            out.get_storer(gruppo).attrs.riepilogo = info["riepilogo"]
            scritti += 1
        if not scritti:
            raise ArchivioNonValido(
                "nessun computer di bordo elaborabile in questo archivio"
            )
    return destinazione


def verifica_integrita(zip_path: Path) -> Dict[str, str]:
    """Controlla l'archivio SENZA estrarlo. Ritorna {pc: "" | motivo del guasto}.

    Serve a bocciare subito in fase di caricamento gli archivi rovinati: il caso
    reale (BF05 del 03/08) ha CRC dello ZIP intatto ma il ``tar.gz`` interno
    troncato, quindi ``testzip()`` da solo non basta e bisogna scorrere il flusso
    compresso. Su archivi da 90 MB costa meno di un secondo.
    """
    try:
        with zipfile.ZipFile(zip_path) as z:
            rotta = z.testzip()
            if rotta:
                raise ArchivioNonValido(f"voce ZIP corrotta: {rotta}")

            interni = [n for n in z.namelist() if re.search(r"_pc\d\.tar\.gz$", n)]
            if not interni:
                raise ArchivioNonValido(
                    "l'archivio non contiene i file dei computer di bordo "
                    "(_pc1.tar.gz / _pc2.tar.gz)"
                )

            esiti: Dict[str, str] = {}
            for nome in sorted(interni):
                pc = re.search(r"_(pc\d)\.tar\.gz$", nome).group(1)
                try:
                    with z.open(nome) as f, gzip.GzipFile(fileobj=f) as g:
                        while g.read(4 << 20):
                            pass
                    esiti[pc] = ""
                except (OSError, EOFError, zlib.error) as e:
                    esiti[pc] = f"archivio troncato o illeggibile ({type(e).__name__})"
            return esiti
    except zipfile.BadZipFile as e:
        raise ArchivioNonValido(f"file ZIP illeggibile: {e}") from e
