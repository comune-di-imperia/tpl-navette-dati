"""Statistiche della sperimentazione, lette dall'applicazione di bordo.

I dati stanno nel database dell'applicazione dei passeggeri, che e' un altro
programma sulla stessa macchina. Qui si legge da un'utenza che vede
**soltanto le due viste anonime** — `vista_utilizzo` e `vista_ricerca` — e a
cui le tabelle con nome, cognome, data di nascita ed email sono negate dal
database stesso. Non e' una cautela nel codice: e' un permesso mancante, e
resta vero anche se qualcuno un domani scrivesse qui la query sbagliata.

E' la stessa promessa che l'informativa fa ai passeggeri: alla ricerca vanno
fascia d'eta', sesso, rapporto con la citta' e professione, mai il nome.

Cosa manca, e va detto invece di nasconderlo: il campo `contesto`, che
dovrebbe portare linea, corsa e fermata dal codice affisso alla fermata, e'
vuoto su tutte le corse registrate finora. La fermata si puo' quindi soltanto
dedurre dalle coordinate della salita, e solo per le corse che ce l'hanno.
"""

from __future__ import annotations

import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import psycopg
except ImportError:  # pragma: no cover - l'ambiente di prova puo' non averlo
    psycopg = None

from . import fermate as elenco_fermate
from . import mappa

# Mezzi che non fanno servizio: il collaudo non deve entrare nei numeri che
# finiscono nella relazione al Ministero.
MEZZI_DI_PROVA = ("BUS-PROVA",)

# Lato della maglia con cui si raggruppano le salite vicine, in gradi.
# 0,0004 gradi di latitudine sono circa 45 metri: abbastanza per tenere insieme
# le salite alla stessa fermata, abbastanza poco per non fonderne due contigue.
MAGLIA_GRADI = 0.0004

ETICHETTE_CATEGORIA = {
    "residente": "Residente",
    "turista": "Turista",
    "pendolare": "Pendolare",
    "occasionale": "Di passaggio",
}

ETICHETTE_PROFESSIONE = {
    "dipendente": "Lavoratore dipendente",
    "autonomo": "Lavoratore autonomo",
    "studente": "Studente",
    "pensionato": "Pensionato",
    "casalingo": "Lavoro domestico",
    "cerca_lavoro": "In cerca di occupazione",
    "altro": "Altro",
}

ETICHETTE_SESSO = {"F": "Femmine", "M": "Maschi", "X": "Non dichiarato"}

ETICHETTE_CHIUSURA = {
    "feedback": "Chiusa con la valutazione",
    "timeout": "Scaduta da sola",
    "nuova_salita": "Sostituita da una nuova salita",
    None: "Ancora aperta",
}

ETICHETTE_LINGUA = {
    "it": "Italiano", "en": "Inglese", "fr": "Francese",
    "de": "Tedesco", "es": "Spagnolo",
}


class DatiNonDisponibili(RuntimeError):
    """Il database dell'applicazione di bordo non risponde."""


def _stringa_connessione() -> str:
    """Parametri di collegamento, tutti da ambiente: qui non c'e' nulla."""
    return (
        f"host={os.environ.get('TPL_STAT_DB_HOST', '127.0.0.1')} "
        f"port={os.environ.get('TPL_STAT_DB_PORT', '5432')} "
        f"dbname={os.environ.get('TPL_STAT_DB_NAME', 'impqr')} "
        f"user={os.environ.get('TPL_STAT_DB_USER', 'tpl_statistiche')} "
        f"password={os.environ.get('TPL_STAT_DB_PASSWORD', '')}"
    )


def _interroga(sql: str, parametri: Tuple = ()) -> List[Tuple]:
    if psycopg is None:
        raise DatiNonDisponibili("il collegamento al database non e' installato")
    if not os.environ.get("TPL_STAT_DB_PASSWORD"):
        raise DatiNonDisponibili("credenziali di lettura non configurate")
    try:
        with psycopg.connect(_stringa_connessione(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, parametri)
                return cur.fetchall()
    except psycopg.Error as errore:
        raise DatiNonDisponibili(str(errore).strip()) from errore


def _filtro_mezzi() -> str:
    """Clausola che tiene fuori i mezzi di collaudo."""
    elenco = ", ".join(f"'{m}'" for m in MEZZI_DI_PROVA)
    return f"mezzo NOT IN ({elenco})"


def _fra_date(colonna: str, periodo: Optional[Tuple]) -> str:
    """Clausola che limita a un periodo, estremi compresi.

    Le date arrivano dal chiamante e non dall'utente: si compongono nella
    query senza parametri perche' sono oggetti `date`, non testo, e passano
    per `isoformat`.
    """
    if not periodo:
        return ""
    da, a = periodo
    return (f" AND {colonna} >= '{da.isoformat()}'"
            f" AND {colonna} < '{(a + timedelta(days=1)).isoformat()}'")


def registrazioni_totali() -> int:
    """Quante persone risultano registrate in tutto, senza limiti di periodo.

    Serve dove si guarda una giornata sola: le due iscrizioni di ieri non
    dicono se il servizio conta cento persone o mille. Si contano come nella
    pagina, cancellazioni comprese, altrimenti due numeri che si chiamano allo
    stesso modo direbbero cose diverse.
    """
    return _interroga("SELECT count(*) FROM vista_registrazioni")[0][0] or 0


def periodo_precedente(da: date, a: date) -> Tuple[date, date]:
    """Il periodo di pari durata che finisce il giorno prima."""
    durata = (a - da).days + 1
    fine = da - timedelta(days=1)
    return (fine - timedelta(days=durata - 1), fine)


def settimana_scorsa(oggi: Optional[date] = None) -> Tuple[date, date]:
    """Da lunedi' a domenica della settimana conclusa."""
    oggi = oggi or datetime.now(timezone.utc).date()
    lunedi = oggi - timedelta(days=oggi.weekday() + 7)
    return (lunedi, lunedi + timedelta(days=6))


def mese_scorso(oggi: Optional[date] = None) -> Tuple[date, date]:
    """Dal primo all'ultimo giorno del mese concluso."""
    oggi = oggi or datetime.now(timezone.utc).date()
    fine = oggi.replace(day=1) - timedelta(days=1)
    return (fine.replace(day=1), fine)


def scostamento(adesso: float, prima: float) -> Dict[str, Any]:
    """Differenza fra due periodi, in valore assoluto e in percentuale.

    Quando il periodo precedente e' a zero la variazione percentuale non
    esiste: dire "piu' infinito" o "piu' cento per cento" sarebbe inventare.
    Si riporta `None` e la pagina scrive che prima non c'era nulla.
    """
    differenza = adesso - prima
    return {
        "adesso": adesso,
        "prima": prima,
        "differenza": round(differenza, 2),
        "percentuale": (round(differenza / prima * 100, 1) if prima else None),
        "verso": "su" if differenza > 0 else ("giu" if differenza < 0 else "pari"),
    }


def _distribuzione(righe: List[Tuple], etichette: Dict[str, str]) -> List[Dict]:
    """Voci ordinate per frequenza, con etichetta leggibile e percentuale."""
    totale = sum(n for _, n in righe) or 1
    voci = []
    for chiave, quante in righe:
        testo = etichettatura(chiave, etichette)
        voci.append({
            "chiave": chiave,
            "etichetta": testo,
            "quante": quante,
            "quota": round(quante * 100.0 / totale, 1),
        })
    return voci


def etichettatura(chiave: Optional[str], etichette: Dict[str, str]) -> str:
    """Nome leggibile della voce.

    Il dizionario si consulta per primo perche' anche l'assenza di valore puo'
    avere un significato preciso: una corsa senza motivo di chiusura non e'
    "non indicato", e' una corsa ancora aperta.
    """
    if chiave in etichette:
        return etichette[chiave]
    if chiave is None:
        return "Non indicato"
    return str(chiave).capitalize()


def _serie_giornaliera(righe: List[Tuple], giorni: int = 30) -> List[Dict]:
    """Conteggi per giorno, con i giorni vuoti al loro posto.

    Senza i buchi il grafico mentirebbe: tre corse in tre giorni consecutivi e
    tre corse in tre settimane disegnerebbero la stessa linea.
    """
    per_giorno = {riga[0]: riga[1] for riga in righe}
    oggi = datetime.now(timezone.utc).date()
    serie = []
    for scarto in range(giorni - 1, -1, -1):
        quel_giorno = oggi - timedelta(days=scarto)
        serie.append({
            "giorno": quel_giorno,
            "quante": per_giorno.get(quel_giorno, 0),
        })
    return serie


def _punti_di_salita(righe: List[Tuple]) -> List[Dict]:
    """Raggruppa le salite vicine fra loro.

    Non si chiamano fermate perche' non lo sappiamo: l'applicazione di bordo
    non registra quale fermata sia stata inquadrata, e questi sono soltanto i
    luoghi dove le salite si addensano. Quando avremo l'elenco delle fermate
    con le loro coordinate, a ogni gruppo si potra' dare il nome giusto.
    """
    gruppi: Dict[Tuple[int, int], List[Tuple[float, float]]] = {}
    for lat, lon in righe:
        if lat is None or lon is None:
            continue
        lat, lon = float(lat), float(lon)
        maglia = (round(lat / MAGLIA_GRADI), round(lon / MAGLIA_GRADI))
        gruppi.setdefault(maglia, []).append((lat, lon))

    punti = []
    for numero, (_, membri) in enumerate(
            sorted(gruppi.items(), key=lambda v: len(v[1]), reverse=True), 1):
        punti.append({
            "numero": numero,
            "quante": len(membri),
            "lat": round(sum(m[0] for m in membri) / len(membri), 6),
            "lon": round(sum(m[1] for m in membri) / len(membri), 6),
        })
    return punti


def raccogli(giorni: int = 30,
             periodo: Optional[Tuple[date, date]] = None,
             confronta: bool = True) -> Dict[str, Any]:
    """Tutti i numeri, per l'intero esercizio o per un periodo.

    Senza `periodo` si guarda tutto quello che c'e', che e' cio' che serve
    alla pagina. Con un periodo si guarda solo quello, e — se `confronta` e'
    vero — si affianca il periodo di pari durata immediatamente precedente:
    e' il confronto che rende leggibile un numero, perche' quaranta corse in
    una settimana dicono poco finche' non si sa quante erano prima.
    """
    filtro = _filtro_mezzi()
    quando = _fra_date("iniziato_il", periodo)

    corse = _interroga(
        f"SELECT count(*), count(DISTINCT date_trunc('day', iniziato_il)), "
        f"count(*) FILTER (WHERE con_valutazione), "
        f"count(*) FILTER (WHERE lat IS NOT NULL), "
        f"min(iniziato_il), max(iniziato_il), "
        f"count(*) FILTER (WHERE terminato_il IS NULL) "
        f"FROM vista_utilizzo WHERE {filtro}{quando}")[0]

    prova = _interroga(
        "SELECT count(*) FROM vista_utilizzo WHERE NOT (" + filtro + ")"
        + quando)[0][0]

    per_giorno = _interroga(
        f"SELECT date_trunc('day', iniziato_il)::date, count(*) "
        f"FROM vista_utilizzo WHERE {filtro}{quando} GROUP BY 1")

    per_ora = _interroga(
        f"SELECT EXTRACT(HOUR FROM iniziato_il)::int, count(*) "
        f"FROM vista_utilizzo WHERE {filtro}{quando} GROUP BY 1 ORDER BY 1")

    per_mezzo = _interroga(
        f"SELECT mezzo, count(*) FROM vista_utilizzo WHERE {filtro}{quando} "
        f"GROUP BY 1 ORDER BY 2 DESC")

    per_chiusura = _interroga(
        f"SELECT chiuso_da, count(*) FROM vista_utilizzo WHERE {filtro}{quando} "
        f"GROUP BY 1 ORDER BY 2 DESC")

    posizioni = _interroga(
        f"SELECT lat, lon FROM vista_utilizzo "
        f"WHERE {filtro}{quando} AND lat IS NOT NULL")

    def caratteristica(colonna: str) -> List[Tuple]:
        return _interroga(
            f"SELECT {colonna}, count(*) FROM vista_utilizzo WHERE {filtro}{quando} "
            f"GROUP BY 1 ORDER BY 2 DESC")

    registrazioni = _interroga(
        "SELECT count(*), count(*) FILTER (WHERE email_verificata), "
        "count(*) FILTER (WHERE cancellato), "
        "count(*) FILTER (WHERE con_provider), "
        "count(*) FILTER (WHERE consenso_il IS NOT NULL), "
        "count(*) FILTER (WHERE consenso_revocato) "
        "FROM vista_registrazioni WHERE true"
        + _fra_date("registrato_il", periodo))[0]

    iscritti_giorno = _interroga(
        "SELECT registrato_il, count(*) FROM vista_registrazioni "
        "WHERE registrato_il IS NOT NULL"
        + _fra_date("registrato_il", periodo) + " GROUP BY 1")

    per_lingua = _interroga(
        "SELECT lingua_consenso, count(*) FROM vista_registrazioni "
        "WHERE lingua_consenso IS NOT NULL"
        + _fra_date("registrato_il", periodo)
        + " GROUP BY 1 ORDER BY 2 DESC")

    def anagrafica(colonna: str) -> List[Tuple]:
        return _interroga(
            f"SELECT {colonna}, count(*) FROM vista_registrazioni "
            f"WHERE true" + _fra_date("registrato_il", periodo)
            + " GROUP BY 1 ORDER BY 2 DESC")

    voti = _interroga(
        "SELECT rating, count(*) FROM vista_ricerca WHERE true"
        + _fra_date("creato_il", periodo) + " GROUP BY 1 ORDER BY 1")
    media_voto = _interroga(
        "SELECT avg(rating) FROM vista_ricerca WHERE true"
        + _fra_date("creato_il", periodo))[0][0]
    commenti = _interroga(
        "SELECT count(*) FROM vista_ricerca WHERE commento IS NOT NULL "
        "AND btrim(commento) <> ''" + _fra_date("creato_il", periodo))[0][0]

    totale_corse = corse[0] or 0
    con_posizione = corse[3] or 0

    # La mappa e' un'immagine composta una volta sola e servita da noi: qui si
    # calcola soltanto dove cadono i punti. Se non c'e', la pagina ripiega
    # sull'elenco delle coordinate.
    # Ogni salita va alla fermata piu' vicina: quelle a zero restano
    # nell'elenco, perche' "nessuno sale qui" e' un risultato, non un vuoto.
    attribuzione = elenco_fermate.conta_salite(posizioni)
    salite = attribuzione["fermate"]
    sfondo = mappa.leggi_riferimento()
    if sfondo:
        sfondo = dict(sfondo)
        sfondo["punti"] = mappa.colloca(
            [f for f in salite if f["quante"]], sfondo)
        sfondo["capisaldi"] = mappa.colloca(
            [f for f in salite if not f["quante"]], sfondo)
        sfondo["tecnici"] = mappa.colloca(attribuzione["tecnici"], sfondo)

    esito = {
        "registrazioni": {
            "totale": registrazioni[0] or 0,
            "verificate": registrazioni[1] or 0,
            "cancellate": registrazioni[2] or 0,
            "con_provider": registrazioni[3] or 0,
            "con_consenso": registrazioni[4] or 0,
            "consenso_revocato": registrazioni[5] or 0,
        },
        "iscritti_per_giorno": _serie_giornaliera(iscritti_giorno, giorni),
        "per_lingua": _distribuzione(per_lingua, ETICHETTE_LINGUA),
        "anagrafica": {
            "fascia_eta": _distribuzione(anagrafica("fascia_eta"), {}),
            "sesso": _distribuzione(anagrafica("sesso"), ETICHETTE_SESSO),
            "categoria": _distribuzione(anagrafica("categoria"),
                                        ETICHETTE_CATEGORIA),
            "professione": _distribuzione(anagrafica("professione"),
                                          ETICHETTE_PROFESSIONE),
        },
        "corse": {
            "totale": totale_corse,
            "giorni_di_servizio": corse[1] or 0,
            "con_valutazione": corse[2] or 0,
            "con_posizione": con_posizione,
            "quota_posizione": round(con_posizione * 100.0 / totale_corse, 1)
                               if totale_corse else 0.0,
            "prima": corse[4],
            "ultima": corse[5],
            "ancora_aperte": corse[6] or 0,
            "di_prova": prova,
        },
        "per_giorno": _serie_giornaliera(per_giorno, giorni),
        "per_ora": [{"ora": o, "quante": n} for o, n in per_ora],
        "per_mezzo": _distribuzione(per_mezzo, {}),
        "per_chiusura": _distribuzione(per_chiusura, ETICHETTE_CHIUSURA),
        "fascia_eta": _distribuzione(caratteristica("fascia_eta"), {}),
        "sesso": _distribuzione(caratteristica("sesso"), ETICHETTE_SESSO),
        "categoria": _distribuzione(caratteristica("categoria"),
                                    ETICHETTE_CATEGORIA),
        "professione": _distribuzione(caratteristica("professione"),
                                      ETICHETTE_PROFESSIONE),
        "fermate": salite,
        "punti_tecnici": attribuzione["tecnici"],
        "salite_lontane": attribuzione["lontane"],
        "mappa": sfondo,
        "voti": {
            "distribuzione": [{"stelle": s, "quante": n} for s, n in voti],
            "totale": sum(n for _, n in voti),
            "media": round(float(media_voto), 2) if media_voto else None,
            "con_commento": commenti,
        },
        "aggiornato": datetime.now(timezone.utc),
        "periodo": {"da": periodo[0], "a": periodo[1]} if periodo else None,
    }

    if periodo and confronta:
        esito["confronto"] = _confronta(esito, periodo)

    return esito


# Le grandezze che ha senso confrontare fra due periodi: quelle di flusso.
# I totali di sempre — quante persone risultano registrate in tutto — non si
# confrontano, perche' crescono e basta.
GRANDEZZE = (
    ("corse", "Salite a bordo", lambda d: d["corse"]["totale"]),
    ("registrazioni", "Nuove registrazioni",
     lambda d: d["registrazioni"]["totale"]),
    ("valutazioni", "Valutazioni ricevute", lambda d: d["voti"]["totale"]),
    ("voto", "Voto medio", lambda d: d["voti"]["media"] or 0),
    ("giorni", "Giorni di esercizio",
     lambda d: d["corse"]["giorni_di_servizio"]),
)


def _confronta(esito: Dict[str, Any],
               periodo: Tuple[date, date]) -> Dict[str, Any]:
    """Affianca al periodo quello di pari durata che lo precede."""
    prima_da, prima_a = periodo_precedente(*periodo)
    prima = raccogli(periodo=(prima_da, prima_a), confronta=False)

    voci = []
    for chiave, etichetta, prendi in GRANDEZZE:
        voci.append({
            "chiave": chiave,
            "etichetta": etichetta,
            **scostamento(prendi(esito), prendi(prima)),
        })

    return {
        "da": prima_da,
        "a": prima_a,
        "voci": voci,
        "fermate": _confronta_fermate(esito, prima),
    }


def _confronta_fermate(adesso: Dict, prima: Dict) -> List[Dict]:
    """Salite per fermata nei due periodi, per vedere dove cambia l'uso."""
    passate = {f["nome"]: f["quante"] for f in prima.get("fermate", [])}
    voci = []
    for fermata in adesso.get("fermate", []):
        voci.append({
            "nome": fermata["nome"],
            **scostamento(fermata["quante"], passate.get(fermata["nome"], 0)),
        })
    return voci
