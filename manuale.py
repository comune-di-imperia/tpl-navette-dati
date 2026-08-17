"""Manuale d'uso dell'applicazione, in italiano e inglese, reso in PDF.

I contenuti stanno in una struttura unica per lingua e vengono resi dallo stesso
modello: cosi' aggiungere una sezione non significa riscrivere l'impaginazione,
e le due versioni non divergono per distrazione.
"""

from __future__ import annotations

import html as _html
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

LINGUE = ("it", "en")

INTESTAZIONI: Dict[str, Dict[str, str]] = {
    "it": {
        "ente": "Comune di Imperia · Direzione Lavori TPL",
        "titolo": "Gestione dati navette a guida autonoma",
        "sottotitolo": "Manuale d'uso",
        "indice": "Indice",
        "aggiornato": "Aggiornato al",
        "pagina": "pagina",
        "di": "di",
        "crediti": "Software Architect: Ing. Carlo Capacci · "
        "Software Coder: Claude.ai",
    },
    "en": {
        "ente": "Municipality of Imperia · Public Transport Works Management",
        "titolo": "Autonomous shuttle data management",
        "sottotitolo": "User manual",
        "indice": "Contents",
        "aggiornato": "Updated",
        "pagina": "page",
        "di": "of",
        "crediti": "Software Architect: Ing. Carlo Capacci · "
        "Software Coder: Claude.ai",
    },
}


def _p(testo: str) -> Tuple[str, Any]:
    return ("p", testo)


def _el(voci: List[str]) -> Tuple[str, Any]:
    return ("elenco", voci)


def _tab(intestazioni: List[str], righe: List[List[str]]) -> Tuple[str, Any]:
    return ("tabella", {"intestazioni": intestazioni, "righe": righe})


def _nota(testo: str) -> Tuple[str, Any]:
    return ("nota", testo)


SEZIONI: Dict[str, List[Dict[str, Any]]] = {
    "it": [
        {
            "titolo": "A cosa serve",
            "blocchi": [
                _p(
                    "L'applicazione raccoglie, analizza e conserva i dati di "
                    "funzionamento delle navette a guida autonoma in "
                    "sperimentazione sul territorio comunale. Ogni giornata di "
                    "esercizio produce dei file che vengono caricati qui: il "
                    "sistema li verifica, ne ricava le grandezze di interesse, "
                    "produce un report in PDF e archivia tutto in modo "
                    "permanente."
                ),
                _p(
                    "Il fine e' la rendicontazione della sperimentazione: i dati "
                    "originali non vengono mai modificati e restano disponibili "
                    "per qualunque rielaborazione futura."
                ),
            ],
        },
        {
            "titolo": "Accesso",
            "blocchi": [
                _p(
                    "L'accesso richiede credenziali personali. Nel campo utente "
                    "si puo' indicare indifferentemente il proprio nome utente "
                    "oppure il proprio indirizzo email."
                ),
                _p(
                    "<b>Prima attivazione.</b> Quando un amministratore crea "
                    "un'utenza, arriva un'email con un collegamento di "
                    "attivazione valido 48 ore, da usare una sola volta. La "
                    "password la sceglie il titolare: nessun altro la conosce, "
                    "nemmeno chi ha creato l'utenza."
                ),
                _p(
                    "<b>Password dimenticata.</b> Dalla pagina di accesso, "
                    "“Password dimenticata?” chiede l'indirizzo email e "
                    "invia un collegamento valido 30 minuti. Per ragioni di "
                    "riservatezza il messaggio a video e' sempre lo stesso, sia "
                    "che l'indirizzo risulti registrato sia che non lo sia."
                ),
                _nota(
                    "La password deve avere almeno 12 caratteri. Una frase che si "
                    "ricorda facilmente e' piu' robusta di una parola breve piena "
                    "di simboli. Al cambio password tutte le sessioni aperte "
                    "vengono chiuse, compresa quella in corso."
                ),
            ],
        },
        {
            "titolo": "Ruoli e permessi",
            "blocchi": [
                _p(
                    "Ogni utenza ha un solo ruolo. Il ruolo determina cosa si "
                    "vede e cosa si puo' fare; le voci di menu non consentite "
                    "non compaiono."
                ),
                _tab(
                    ["Funzione", "Amministratore", "Tecnico", "Consultazione"],
                    [
                        [
                            "Consultare cruscotto, elaborazioni e archivio",
                            "si",
                            "si",
                            "si",
                        ],
                        ["Scaricare i report e i file archiviati", "si", "si", "si"],
                        ["Caricare i file delle navette", "si", "si", "no"],
                        [
                            "Leggere ed esportare il registro attivita'",
                            "si",
                            "no",
                            "no",
                        ],
                        ["Gestire le utenze", "si", "no", "no"],
                    ],
                ),
                _p(
                    "Possono coesistere piu' amministratori. Il sistema impedisce "
                    "di rimanere senza: l'ultimo amministratore attivo non puo' "
                    "essere sospeso, archiviato o declassato."
                ),
            ],
        },
        {
            "titolo": "Caricare i dati di una giornata",
            "blocchi": [
                _p(
                    "<b>Ogni navetta produce due file al giorno</b>, uno per "
                    "computer di bordo. I due file vanno caricati <b>insieme</b>: "
                    "uno contiene la telemetria del veicolo, l'altro la "
                    "localizzazione, e solo uniti danno un report completo."
                ),
                _p(
                    "Il confezionamento non e' uniforme fra le navette: alcune "
                    "consegnano <code>_pc1.tar.gz</code> e <code>_pc2.tar.gz</code>, "
                    "altre <code>_pc1.zip</code> e <code>_pc2.zip</code>. Vanno bene "
                    "entrambe le forme, non c'e' nulla da convertire."
                ),
                _p(
                    "Si trascinano nel riquadro del cruscotto, oppure si "
                    "selezionano con “scegli dal computer”. Si possono "
                    "trascinare in una volta i file di piu' navette o di piu' "
                    "giornate: vengono raggruppati automaticamente in base al "
                    "nome, una elaborazione per navetta e per giornata."
                ),
                _p(
                    "In alternativa si accetta il file <code>.zip</code> che "
                    "raccoglie i due <code>.tar.gz</code> di una giornata."
                ),
                _p(
                    "<b>Nome dei file.</b> Deve essere quello prodotto "
                    "dall'estrattore: <code>&lt;navetta&gt;_&lt;data&gt;_&lt;VIN&gt;"
                    "_&lt;data&gt;_&lt;ora&gt;</code>. Navetta, veicolo e giornata "
                    "vengono letti da li', quindi i file non vanno rinominati."
                ),
                _p(
                    "<b>Controllo automatico.</b> Prima di accodare il lavoro il "
                    "sistema verifica che gli archivi non siano danneggiati. Se "
                    "sono tutti illeggibili il caricamento viene rifiutato subito, "
                    "con l'indicazione di richiedere di nuovo i file alla filiera. "
                    "Se lo e' solo uno dei due, l'elaborazione prosegue sul resto "
                    "e la mancanza viene segnalata nel report."
                ),
                _nota(
                    "Durante il trasferimento non chiudere la pagina: il browser "
                    "avvisa. Una volta che i file sono arrivati l'elaborazione "
                    "prosegue sul server anche navigando altrove, e rientrando nel "
                    "cruscotto lo stato si aggiorna da solo."
                ),
            ],
        },
        {
            "titolo": "Leggere il report",
            "blocchi": [
                _p(
                    "Per ogni elaborazione viene prodotto un report in PDF, "
                    "scaricabile dalla pagina Report singolarmente o tutto insieme "
                    "in un archivio compresso."
                ),
                _p(
                    "<b>Guida autonoma e manuale.</b> La ripartizione e' calcolata "
                    "sul tempo effettivo di marcia e sulla percorrenza, non sul "
                    "numero di rilevazioni: il veicolo resta fermo per gran parte "
                    "della giornata, quindi contare le rilevazioni misurerebbe "
                    "soprattutto in che modalita' e' stato parcheggiato."
                ),
                _p("<b>Grandezze principali:</b>"),
                _tab(
                    ["Grandezza", "Significato"],
                    [
                        [
                            "Distanza percorsa",
                            "ricavata dalla posizione; l'odometro di bordo, quando "
                            "disponibile, la conferma",
                        ],
                        [
                            "Accelerazione e decelerazione",
                            "derivate dalla velocita' misurata a bordo, non dalla "
                            "posizione, che sarebbe troppo rumorosa",
                        ],
                        [
                            "Regime di rotazione",
                            "ricavato dalla velocita' e dalla geometria della ruota; "
                            "finche' il rapporto di riduzione non e' noto il valore "
                            "e' riferito alla ruota, e il report lo dichiara",
                        ],
                        [
                            "Beccheggio",
                            "inclinazione longitudinale, misurata mentre il veicolo "
                            "e' in movimento",
                        ],
                        [
                            "Qualita' del posizionamento",
                            "affidabilita' del sistema di navigazione e ritardo "
                            "delle correzioni satellitari",
                        ],
                    ],
                ),
                _p(
                    "In coda al report compaiono le eventuali segnalazioni: dati "
                    "mancanti, archivi danneggiati, campioni scartati perche' "
                    "implausibili."
                ),
            ],
        },
        {
            "titolo": "Archivio",
            "blocchi": [
                _p(
                    "La voce Archivio permette di navigare lo spazio di "
                    "conservazione e di scaricare qualunque file gia' archiviato. "
                    "E' di sola lettura: da qui non si carica e non si cancella "
                    "nulla."
                ),
                _tab(
                    ["Cartella", "Contenuto"],
                    [
                        ["originali/", "i file consegnati, mai modificati"],
                        ["elaborati/", "i dati con le grandezze calcolate"],
                        [
                            "registro/",
                            "storico delle attivita', riservato agli amministratori",
                        ],
                    ],
                ),
                _p(
                    "L'organizzazione e' per navetta e per <b>giornata dei dati</b>, "
                    "non per data di caricamento: un file caricato in ritardo si "
                    "colloca comunque accanto agli altri della sua giornata."
                ),
            ],
        },
        {
            "titolo": "Registro attivita'",
            "blocchi": [
                _p(
                    "Il registro annota chi ha fatto cosa e da quale indirizzo: "
                    "accessi riusciti e respinti, caricamenti, scarichi, modifiche "
                    "alle utenze. E' riservato agli amministratori ed e' "
                    "esportabile in formato CSV, gia' predisposto per l'apertura "
                    "diretta in Excel."
                ),
                _p(
                    "A video restano i mesi piu' recenti. Ogni mese concluso viene "
                    "copiato nell'archivio in forma compressa e vi resta senza "
                    "scadenza: la copia viene sempre verificata prima che le voci "
                    "vengano tolte dalla consultazione."
                ),
            ],
        },
        {
            "titolo": "Gestione delle utenze",
            "blocchi": [
                _p(
                    "Riservata agli amministratori. Alla creazione non si sceglie "
                    "una password per altri: parte un invito e la sceglie il "
                    "titolare."
                ),
                _tab(
                    ["Azione", "Effetto"],
                    [
                        ["Sospendi", "blocca l'accesso, subito; reversibile"],
                        [
                            "Archivia",
                            "esclude l'utenza mantenendo intatti i riferimenti "
                            "storici alle sue lavorazioni",
                        ],
                        ["Sblocca", "azzera il blocco dopo troppi tentativi falliti"],
                        ["Invia recupero", "recapita un collegamento per la password"],
                        ["Cambia ruolo", "ha effetto immediato sulle sessioni aperte"],
                    ],
                ),
                _nota(
                    "Le utenze non si cancellano: verrebbero meno i riferimenti "
                    "delle elaborazioni gia' svolte e la tracciabilita' andrebbe "
                    "persa. Si archiviano."
                ),
            ],
        },
        {
            "titolo": "Domande frequenti",
            "blocchi": [
                _p(
                    "<b>Ho caricato un solo file della giornata.</b> Funziona, ma "
                    "il report copre solo quel computer di bordo. Caricando poi "
                    "l'altro si ottiene una seconda elaborazione separata."
                ),
                _p(
                    "<b>Il caricamento e' stato rifiutato.</b> I motivi possibili "
                    "sono tre: estensione non ammessa, nome non conforme a quello "
                    "prodotto dall'estrattore, oppure archivi illeggibili. Il "
                    "messaggio a video indica quale."
                ),
                _p(
                    "<b>L'elaborazione risulta non riuscita.</b> Il motivo compare "
                    "nel cruscotto. I file caricati restano sul server e "
                    "l'operazione si puo' ripetere."
                ),
                _p(
                    "<b>Le credenziali non vengono accettate.</b> Verificare di "
                    "non aver superato i tentativi consentiti: dopo alcuni "
                    "fallimenti l'utenza si blocca per qualche minuto. Un "
                    "amministratore puo' sbloccarla subito."
                ),
            ],
        },
    ],
    "en": [
        {
            "titolo": "Purpose",
            "blocchi": [
                _p(
                    "This application collects, analyses and preserves the "
                    "operating data of the autonomous shuttles being trialled "
                    "within the municipal area. Each day of service produces "
                    "files that are uploaded here: the system checks them, "
                    "derives the quantities of interest, produces a PDF report "
                    "and archives everything permanently."
                ),
                _p(
                    "The purpose is to document the trial: original data is never "
                    "altered and remains available for any future reprocessing."
                ),
            ],
        },
        {
            "titolo": "Signing in",
            "blocchi": [
                _p(
                    "Access requires personal credentials. In the user field you "
                    "may enter either your user name or your email address."
                ),
                _p(
                    "<b>First activation.</b> When an administrator creates an "
                    "account, an email arrives with an activation link valid for "
                    "48 hours and usable once. The password is chosen by the "
                    "account holder: nobody else knows it, not even whoever "
                    "created the account."
                ),
                _p(
                    "<b>Forgotten password.</b> From the sign-in page, "
                    "“Password dimenticata?” asks for your email address "
                    "and sends a link valid for 30 minutes. For privacy reasons "
                    "the on-screen message is always the same, whether or not the "
                    "address is registered."
                ),
                _nota(
                    "Passwords must be at least 12 characters. A phrase you can "
                    "remember is stronger than a short word full of symbols. When "
                    "the password changes, every open session is closed, including "
                    "the current one."
                ),
            ],
        },
        {
            "titolo": "Roles and permissions",
            "blocchi": [
                _p(
                    "Each account has exactly one role. The role determines what "
                    "is visible and what can be done; menu entries that are not "
                    "permitted are not shown."
                ),
                _tab(
                    ["Function", "Administrator", "Technician", "Read-only"],
                    [
                        [
                            "View dashboard, processing runs and archive",
                            "yes",
                            "yes",
                            "yes",
                        ],
                        ["Download reports and archived files", "yes", "yes", "yes"],
                        ["Upload shuttle files", "yes", "yes", "no"],
                        ["Read and export the activity log", "yes", "no", "no"],
                        ["Manage accounts", "yes", "no", "no"],
                    ],
                ),
                _p(
                    "Several administrators may coexist. The system prevents being "
                    "left without one: the last active administrator cannot be "
                    "suspended, archived or demoted."
                ),
            ],
        },
        {
            "titolo": "Uploading a day of data",
            "blocchi": [
                _p(
                    "<b>Each shuttle produces two files per day</b>, one per "
                    "on-board computer. The two files must be uploaded "
                    "<b>together</b>: one holds vehicle telemetry, the other "
                    "localisation, and only combined do they yield a complete "
                    "report."
                ),
                _p(
                    "Packaging is not uniform across shuttles: some deliver "
                    "<code>_pc1.tar.gz</code> and <code>_pc2.tar.gz</code>, others "
                    "<code>_pc1.zip</code> and <code>_pc2.zip</code>. Both forms "
                    "are accepted, nothing needs converting."
                ),
                _p(
                    "Drag them onto the dashboard drop area, or pick them with "
                    "“scegli dal computer”. You may drop files from "
                    "several shuttles or several days at once: they are grouped "
                    "automatically by name, one processing run per shuttle per day."
                ),
                _p(
                    "Alternatively the <code>.zip</code> file bundling the two "
                    "<code>.tar.gz</code> of a day is also accepted."
                ),
                _p(
                    "<b>File names.</b> They must be the ones produced by the "
                    "extractor: <code>&lt;shuttle&gt;_&lt;date&gt;_&lt;VIN&gt;"
                    "_&lt;date&gt;_&lt;time&gt;</code>. Shuttle, vehicle and day "
                    "are read from there, so files must not be renamed."
                ),
                _p(
                    "<b>Automatic check.</b> Before queueing the work the system "
                    "verifies that the archives are not damaged. If all of them "
                    "are unreadable the upload is rejected immediately, with a "
                    "note to request the files again from the supply chain. If "
                    "only one is damaged, processing continues on the rest and the "
                    "gap is reported."
                ),
                _nota(
                    "Do not close the page while files are being transferred: the "
                    "browser will warn you. Once they have arrived, processing "
                    "continues on the server even if you navigate elsewhere, and "
                    "the dashboard updates itself when you return."
                ),
            ],
        },
        {
            "titolo": "Reading the report",
            "blocchi": [
                _p(
                    "Every processing run produces a PDF report, downloadable from "
                    "the Report page individually or all together as a compressed "
                    "archive."
                ),
                _p(
                    "<b>Autonomous and manual driving.</b> The split is computed on "
                    "actual running time and on distance, not on the number of "
                    "samples: the vehicle is stationary for most of the day, so "
                    "counting samples would mostly measure which mode it was "
                    "parked in."
                ),
                _p("<b>Main quantities:</b>"),
                _tab(
                    ["Quantity", "Meaning"],
                    [
                        [
                            "Distance travelled",
                            "derived from position; the on-board odometer, where "
                            "available, confirms it",
                        ],
                        [
                            "Acceleration and braking",
                            "derived from the speed measured on board, not from "
                            "position, which would be too noisy",
                        ],
                        [
                            "Rotational speed",
                            "derived from speed and wheel geometry; until the "
                            "reduction ratio is known the figure refers to the "
                            "wheel, and the report states so",
                        ],
                        [
                            "Pitch",
                            "longitudinal inclination, measured while the vehicle "
                            "is moving",
                        ],
                        [
                            "Positioning quality",
                            "reliability of the navigation system and age of the "
                            "satellite corrections",
                        ],
                    ],
                ),
                _p(
                    "Any findings appear at the end of the report: missing data, "
                    "damaged archives, samples discarded as implausible."
                ),
            ],
        },
        {
            "titolo": "Archive",
            "blocchi": [
                _p(
                    "The Archive entry lets you browse the storage space and "
                    "download any archived file. It is read-only: nothing can be "
                    "uploaded or deleted from here."
                ),
                _tab(
                    ["Folder", "Contents"],
                    [
                        ["originali/", "the files as delivered, never altered"],
                        ["elaborati/", "the data with the computed quantities"],
                        ["registro/", "activity history, administrators only"],
                    ],
                ),
                _p(
                    "Organisation is by shuttle and by <b>the day the data refers "
                    "to</b>, not by upload date: a file uploaded late still sits "
                    "next to the others of its day."
                ),
            ],
        },
        {
            "titolo": "Activity log",
            "blocchi": [
                _p(
                    "The log records who did what and from which address: "
                    "successful and rejected sign-ins, uploads, downloads, account "
                    "changes. It is restricted to administrators and can be "
                    "exported as CSV, ready to open directly in Excel."
                ),
                _p(
                    "The most recent months stay on screen. Each completed month is "
                    "copied to the archive in compressed form and kept there "
                    "indefinitely: the copy is always verified before entries are "
                    "removed from the on-screen view."
                ),
            ],
        },
        {
            "titolo": "Account management",
            "blocchi": [
                _p(
                    "Restricted to administrators. No password is chosen on behalf "
                    "of anyone: an invitation is sent and the holder chooses it."
                ),
                _tab(
                    ["Action", "Effect"],
                    [
                        ["Suspend", "blocks access immediately; reversible"],
                        [
                            "Archive",
                            "removes the account from use while keeping historical "
                            "references to its work intact",
                        ],
                        [
                            "Unblock",
                            "clears the lockout after too many failed attempts",
                        ],
                        ["Send recovery", "delivers a password link"],
                        ["Change role", "takes effect immediately on open sessions"],
                    ],
                ),
                _nota(
                    "Accounts are not deleted: references from work already carried "
                    "out would break and traceability would be lost. They are "
                    "archived instead."
                ),
            ],
        },
        {
            "titolo": "Frequently asked questions",
            "blocchi": [
                _p(
                    "<b>I only uploaded one file for the day.</b> It works, but the "
                    "report covers that on-board computer only. Uploading the other "
                    "one later produces a second, separate processing run."
                ),
                _p(
                    "<b>The upload was rejected.</b> There are three possible "
                    "reasons: extension not accepted, name not matching what the "
                    "extractor produces, or unreadable archives. The on-screen "
                    "message says which."
                ),
                _p(
                    "<b>Processing failed.</b> The reason is shown on the "
                    "dashboard. The uploaded files remain on the server and the "
                    "operation can be repeated."
                ),
                _p(
                    "<b>My credentials are not accepted.</b> Check you have not "
                    "exceeded the allowed attempts: after a few failures the "
                    "account locks for some minutes. An administrator can unlock "
                    "it immediately."
                ),
            ],
        },
    ],
}


FOGLIO = """
@page {
  size: A4;
  margin: 2cm 2cm 2.2cm;
  @bottom-left { content: "__ENTE__"; font-size: 7.5pt; color: #8a94a3; }
  @bottom-right {
    content: "__PAGINA__ " counter(page) " __DI__ " counter(pages);
    font-size: 7.5pt; color: #8a94a3;
  }
}
@page :first { margin-top: 4.5cm; }

body { font-family: "DejaVu Sans", sans-serif; font-size: 10pt; color: #1b2430;
       line-height: 1.55; }

.copertina { border-bottom: 4px solid #1f5f9e; padding-bottom: 16px;
             margin-bottom: 26px; }
.copertina .ente { font-size: 9pt; color: #6b7789; text-transform: uppercase;
                   letter-spacing: 1.4px; }
.copertina h1 { font-size: 23pt; color: #1f5f9e; margin: 10px 0 2px;
                line-height: 1.15; }
.copertina .sottotitolo { font-size: 13pt; color: #46505f; }
.copertina .data { font-size: 8.5pt; color: #8a94a3; margin-top: 14px; }

.indice { background: #f4f7fb; border: 1px solid #d8e0ea; border-radius: 6px;
          padding: 14px 18px; margin-bottom: 26px; }
.indice h2 { font-size: 10pt; margin: 0 0 8px; border: 0; padding: 0;
             text-transform: uppercase; letter-spacing: .8px; color: #6b7789; }
.indice ol { margin: 0; padding-left: 20px; font-size: 9.5pt; }
.indice li { margin-bottom: 3px; }

h2 { font-size: 13pt; color: #1f5f9e; margin: 26px 0 8px;
     border-bottom: 1px solid #d8e0ea; padding-bottom: 4px;
     page-break-after: avoid; }
p { margin: 0 0 9px; text-align: justify; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 8.5pt;
       background: #f4f7fb; padding: 1px 4px; border-radius: 3px; }

ul.punti { margin: 0 0 10px; padding-left: 18px; }
ul.punti li { margin-bottom: 4px; }

table { border-collapse: collapse; width: 100%; margin: 10px 0 12px;
        font-size: 9pt; page-break-inside: avoid; }
th { background: #eef2f7; text-align: left; padding: 6px 8px; color: #46505f;
     border-bottom: 1px solid #d8e0ea; font-size: 8.5pt; }
td { padding: 5px 8px; border-bottom: 1px solid #e9eef4; vertical-align: top; }
tbody tr:nth-child(even) { background: #fafbfd; }
td.si { color: #1c7c4a; font-weight: bold; }
td.no { color: #b3261e; }
th:not(:first-child), td:not(:first-child) { text-align: center; }
table.due th:not(:first-child), table.due td:not(:first-child) { text-align: left; }

.nota { background: #f4f7fb; border-left: 3px solid #1f5f9e; padding: 9px 12px;
        margin: 10px 0 12px; font-size: 9pt; color: #46505f;
        border-radius: 0 4px 4px 0; page-break-inside: avoid; }

.crediti { margin-top: 30px; padding-top: 10px; border-top: 1px solid #d8e0ea;
           font-size: 8pt; color: #8a94a3; text-align: center; }
"""


def _e(testo: str) -> str:
    return _html.escape(str(testo))


def _cella(testo: str) -> str:
    """Le colonne si/no diventano segni, cosi' la tabella si legge a colpo d'occhio."""
    if testo in ("si", "yes"):
        return '<td class="si">✔</td>'
    if testo in ("no",):
        return '<td class="no">✘</td>'
    return f"<td>{testo}</td>"


def _blocco(tipo: str, dato: Any) -> str:
    if tipo == "p":
        return f"<p>{dato}</p>"
    if tipo == "nota":
        return f'<div class="nota">{dato}</div>'
    if tipo == "elenco":
        voci = "".join(f"<li>{v}</li>" for v in dato)
        return f'<ul class="punti">{voci}</ul>'
    if tipo == "tabella":
        intestazioni = "".join(f"<th>{_e(h)}</th>" for h in dato["intestazioni"])
        righe = "".join(
            "<tr>" + "".join(_cella(c) for c in riga) + "</tr>"
            for riga in dato["righe"]
        )
        # due sole colonne: sono descrizioni, non un confronto da incolonnare
        classe = " class='due'" if len(dato["intestazioni"]) == 2 else ""
        return (
            f"<table{classe}><thead><tr>{intestazioni}</tr></thead>"
            f"<tbody>{righe}</tbody></table>"
        )
    raise ValueError(f"blocco sconosciuto: {tipo}")


def componi(lingua: str = "it", quando: str = "") -> str:
    """HTML del manuale nella lingua richiesta."""
    if lingua not in LINGUE:
        raise ValueError(f"lingua non prevista: {lingua}")
    testi = INTESTAZIONI[lingua]
    sezioni = SEZIONI[lingua]
    quando = quando or datetime.now().strftime("%d/%m/%Y")

    indice = "".join(f"<li>{_e(s['titolo'])}</li>" for s in sezioni)
    corpo = "".join(
        f"<h2>{_e(s['titolo'])}</h2>"
        + "".join(_blocco(tipo, dato) for tipo, dato in s["blocchi"])
        for s in sezioni
    )

    foglio = (
        FOGLIO.replace("__ENTE__", testi["ente"])
        .replace("__PAGINA__", testi["pagina"])
        .replace("__DI__", testi["di"])
    )

    return f"""<!DOCTYPE html><html lang="{lingua}"><head><meta charset="utf-8">
<title>{_e(testi["titolo"])} - {_e(testi["sottotitolo"])}</title>
<style>{foglio}</style></head><body>
<div class="copertina">
  <div class="ente">{_e(testi["ente"])}</div>
  <h1>{_e(testi["titolo"])}</h1>
  <div class="sottotitolo">{_e(testi["sottotitolo"])}</div>
  <div class="data">{_e(testi["aggiornato"])} {_e(quando)}</div>
</div>
<div class="indice">
  <h2>{_e(testi["indice"])}</h2>
  <ol>{indice}</ol>
</div>
{corpo}
<div class="crediti">{_e(testi["crediti"])}</div>
</body></html>"""


def genera(lingua: str, destinazione: Path) -> Path:
    """Scrive il PDF del manuale."""
    from weasyprint import HTML

    destinazione.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=componi(lingua)).write_pdf(str(destinazione))
    return destinazione
