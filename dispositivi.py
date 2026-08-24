"""Dispositivi di bordo abilitati a leggere i codici di salita.

L'abilitazione sta nel **telefono**, non nella persona: un altro operatore che
prende in mano lo stesso telefono di servizio non deve fare nulla. Serve un
collegamento nuovo solo quando cambia il telefono o entra in servizio un mezzo
nuovo, quindi qualche volta l'anno.

La gestione vera e propria vive nell'applicazione di bordo, che ha una sua
utenza di servizio: qui non se ne duplica la logica, si chiede a lei di operare.

Questa applicazione gira volutamente senza la facolta' di elevare i privilegi
(`NoNewPrivileges`), perche' riceve caricamenti da rete: non puo' quindi
richiamare da sola il comando di gestione. Deposita invece la richiesta in una
cartella condivisa, dove un servizio di supporto - quello si' autorizzato - la
esegue e lascia la risposta accanto. L'applicazione non acquista nuovi poteri:
ottiene una risposta.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("tpl.dispositivi")

CODA = Path(os.environ.get("TPL_DISPOSITIVI_CODA", "/var/lib/tpl-dispositivi"))
RICHIESTE = CODA / "richieste"
RISPOSTE = CODA / "risposte"

# Il servizio di supporto rilegge la cartella circa ogni secondo: oltre questo
# tempo conviene dire che non ha risposto, invece di lasciare la pagina appesa.
ATTESA_S = 20.0
PAUSA_S = 0.2


class ErroreDispositivi(RuntimeError):
    """Il servizio di supporto non risponde o ha rifiutato l'operazione."""


def disponibile() -> bool:
    """Vero se la cartella condivisa esiste ed e' scrivibile."""
    return RICHIESTE.is_dir() and os.access(RICHIESTE, os.W_OK)


def _chiedi(richiesta: Dict[str, Any]) -> str:
    if not disponibile():
        raise ErroreDispositivi(
            "Il servizio di gestione dei dispositivi non e' attivo su questo "
            "server: la funzione e' disponibile solo dove gira l'applicazione "
            "di bordo."
        )

    nome = f"{time.time_ns()}-{uuid.uuid4().hex[:8]}.json"
    provvisorio = RICHIESTE / (nome + ".parziale")
    definitivo = RICHIESTE / nome
    try:
        provvisorio.write_text(json.dumps(richiesta), encoding="utf-8")
        # il servizio deve trovare un file gia' completo, mai a meta'
        provvisorio.replace(definitivo)
    except OSError as errore:
        raise ErroreDispositivi(f"Richiesta non depositata: {errore}") from errore

    attesa_fino = time.monotonic() + ATTESA_S
    risposta_file = RISPOSTE / nome
    while time.monotonic() < attesa_fino:
        if risposta_file.exists():
            try:
                risposta = json.loads(risposta_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                time.sleep(PAUSA_S)
                continue
            finally:
                risposta_file.unlink(missing_ok=True)

            if risposta.get("esito") == "ok":
                return risposta.get("uscita", "")
            raise ErroreDispositivi(risposta.get("messaggio") or "Operazione rifiutata.")
        time.sleep(PAUSA_S)

    definitivo.unlink(missing_ok=True)
    raise ErroreDispositivi(
        "Il servizio di gestione dei dispositivi non ha risposto. "
        "Verificare che sia in esecuzione."
    )


def _esegui(*argomenti: str) -> str:
    """Traduce gli argomenti del comando nella richiesta da depositare."""
    azione = argomenti[0]
    richiesta: Dict[str, Any] = {"azione": azione}
    if azione == "crea":
        richiesta["etichetta"] = argomenti[2]
        richiesta["mezzo"] = argomenti[4]
    elif azione in ("rigenera", "revoca"):
        richiesta["id"] = argomenti[1]
    return _chiedi(richiesta)


_INTESTAZIONI = ("id", "etichetta", "mezzo", "stato", "ultimo uso")


def _colonne(intestazione: str) -> List[tuple]:
    """Dove comincia ogni colonna, leggendolo dall'intestazione.

    Tagliare per posizione invece che per spazi: i valori possono contenere
    spazi loro stessi - un mezzo si chiama "BUS 01" - e separarli a spazi
    spezzerebbe il mezzo attribuendone un pezzo alla colonna successiva.
    """
    inizi = []
    for nome in _INTESTAZIONI:
        dove = intestazione.find(nome, inizi[-1][1] if inizi else 0)
        if dove < 0:
            return []
        inizi.append((nome, dove))
    return [
        (nome, inizio, inizi[i + 1][1] if i + 1 < len(inizi) else None)
        for i, (nome, inizio) in enumerate(inizi)
    ]


def elenco() -> List[Dict[str, str]]:
    """Dispositivi registrati, con stato e ultimo utilizzo."""
    righe = _esegui("elenco").splitlines()
    if not righe:
        return []

    colonne = _colonne(righe[0])
    if not colonne:
        logger.warning(
            "Intestazione dell'elenco non riconosciuta: formato cambiato?",
            extra={"context": {"riga": righe[0][:120]}},
        )
        return []

    trovati = []
    for riga in righe[1:]:
        if not riga.strip():
            continue
        voce = {
            nome.replace(" ", "_"): riga[inizio:fine].strip()
            for nome, inizio, fine in colonne
        }
        if not re.fullmatch(r"[0-9a-f-]{36}", voce.get("id", "")):
            continue
        voce["ultimo_uso"] = voce.get("ultimo_uso") or "mai"
        trovati.append(voce)
    return trovati



_COLLEGAMENTO = re.compile(r"https?://\S+")


def crea(etichetta: str, mezzo: str) -> Dict[str, Any]:
    """Registra un dispositivo e restituisce il collegamento di attivazione."""
    etichetta = (etichetta or "").strip()
    mezzo = (mezzo or "").strip().upper()
    if not etichetta or not mezzo:
        raise ValueError("Servono sia l'etichetta sia il codice del mezzo.")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9 _-]{0,29}", mezzo):
        raise ValueError(
            "Il codice del mezzo puo' contenere lettere, cifre, spazi, trattini "
            "e trattini bassi, per un massimo di 30 caratteri."
        )
    if len(etichetta) > 60:
        raise ValueError("L'etichetta e' troppo lunga: massimo 60 caratteri.")

    uscita = _esegui("crea", "--etichetta", etichetta, "--mezzo", mezzo)
    trovato = _COLLEGAMENTO.search(uscita)
    return {
        "collegamento": trovato.group(0) if trovato else "",
        "uscita": uscita.strip(),
        "etichetta": etichetta,
        "mezzo": mezzo,
    }


def rigenera(identificativo: str) -> Dict[str, Any]:
    """Nuovo collegamento di attivazione: telefono sostituito o dati cancellati."""
    _verifica_id(identificativo)
    uscita = _esegui("rigenera", identificativo)
    trovato = _COLLEGAMENTO.search(uscita)
    return {
        "collegamento": trovato.group(0) if trovato else "",
        "uscita": uscita.strip(),
    }


def revoca(identificativo: str) -> str:
    """Disabilita un dispositivo. Da usare subito se un telefono si smarrisce."""
    _verifica_id(identificativo)
    return _esegui("revoca", identificativo).strip()


def _verifica_id(identificativo: str) -> None:
    if not re.fullmatch(r"[0-9a-f-]{36}", (identificativo or "").strip()):
        raise ValueError("Identificativo del dispositivo non valido.")


def qr_svg(collegamento: str, scala: int = 6) -> str:
    """Codice QR del collegamento, in SVG da inserire nella pagina.

    Il QR si legge direttamente dallo schermo: e' il modo consigliato di
    abilitare un telefono. Il collegamento in chiaro compare comunque sotto,
    ma inviarlo per messaggio lo lascerebbe in una chat per sempre.
    """
    import io

    import segno

    # segno scrive byte anche per l'SVG, quindi il contenitore dev'essere
    # binario: con uno di testo solleva "string argument expected, got bytes".
    disegno = io.BytesIO()
    segno.make(collegamento, error="m").save(
        disegno, kind="svg", scale=scala, border=2, xmldecl=False, svgns=True
    )
    return disegno.getvalue().decode("utf-8")
