# tpl-navette-dati

Applicazione per il caricamento e l'analisi dei dati di funzionamento delle
navette a guida autonoma del **Comune di Imperia**, in esercizio su
`tpl.comune.imperia.it`.

> **Provenienza.** Software sviluppato per il Comune di Imperia nell'ambito
> della sperimentazione del trasporto pubblico locale a guida autonoma
> (decreto PCM/DTD n. 94/2026), su incarico di Direzione Lavori affidato
> all'Ing. Carlo Capacci. Questa è la copia dell'autore, distribuita con
> licenza EUPL-1.2 come previsto dall'articolo 69 del Codice
> dell'amministrazione digitale.

## A che cosa serve

Ogni giorno le navette producono archivi con i parametri di funzionamento
registrati a bordo. L'applicazione li riceve da un riquadro
*trascina-e-rilascia*, li analizza, calcola le grandezze che nei dati grezzi
non ci sono, archivia tutto su storage S3 e produce un rapporto in PDF. Serve
alla rendicontazione della sperimentazione: gli operatori del Comune devono
poter caricare i file senza passare dalla riga di comando.

## Come sono fatti i dati

Il formato non è documentato da nessuna parte: è stato ricostruito aprendo i
file. Ogni navetta produce **due archivi al giorno**, uno per computer di
bordo:

```
<navetta>_<data>_<VIN>_<data>_<ora>.zip
  ├── ..._pc1.tar.gz  →  data-extractor_<data>_<ora>.h5
  └── ..._pc2.tar.gz  →  data-extractor_<data>_<ora>.h5
```

Ogni `.h5` è un *HDFStore* pandas: un gruppo per VIN e, sotto, una tabella per
segnale, campionata a **10 Hz**. I due computer esportano segnali diversi —
telemetria (28 segnali) e localizzazione (11 segnali) — e **quale sia l'uno o
l'altro non è deducibile dal nome del file**: l'applicazione lo riconosce dai
segnali presenti.

Tre unità di misura si prestano a errori grossolani e vanno convertite:
la velocità è in **m/s**, l'odometro in **metri**, gli angoli di assetto in
**radianti**.

Infine, un archivio "giornaliero" può contenere campioni di **più giorni**, con
lunghe pause: la velocità non va calcolata a cavallo delle soste, o si
ottengono picchi inventati.

## Che cosa calcola

Accelerazione, giri motore e beccheggio, che nei dati di bordo non compaiono.

- **Accelerazione**: derivata dalla velocità misurata a bordo, non dalla
  posizione — derivare due volte le coordinate amplificherebbe il rumore del
  posizionamento. I campioni a cavallo di una pausa vengono azzerati.
- **Giri motore**: nessun segnale di regime esiste fra quelli esportati, quindi
  si ricavano dalla geometria della ruota. Finché il rapporto di riduzione non
  è noto il valore prodotto è il regime della **ruota**, e il rapporto lo
  dichiara esplicitamente invece di far finta di nulla.
- **Beccheggio**: fra i tre angoli di assetto la documentazione non dice quale
  sia quale; la corrispondenza è stata stabilita correlando ciascun angolo con
  la pendenza ricavata dalle coordinate.

Le percentuali fra guida autonoma e manuale sono pesate con la **durata** di
ogni campione, non contando i campioni: un archivio che copre giorni di sosta
darebbe altrimenti percentuali prive di senso.

## Struttura

| Modulo | Ruolo |
|---|---|
| `app.py` | interfaccia web: accesso, cruscotto, coda, registro |
| `analisi.py` | lettura archivi, riconoscimento computer, calcolo grandezze |
| `pipeline.py` | orchestrazione: analisi → elaborato → archiviazione → rapporto |
| `referto.py` | composizione del rapporto PDF |
| `esplora.py` | consultazione dell'archivio S3 |
| `casella.py` | stato della sorveglianza della casella e destinatari avvisi |
| `archivio_registro.py` | archiviazione mensile del registro attività |
| `db.py`, `permessi.py` | utenze, ruoli, registro |
| `posta.py`, `manuale.py` | invii e manuale d'uso |
| `dispositivi.py` | telefoni di bordo abilitati a leggere i codici di salita |
| `deploy/` | unità systemd, vhost, logrotate, fail2ban, servizio di supporto |

## Note di esercizio

Il servizio va avviato con **un solo processo** (più thread): la coda di
elaborazione vive nel processo, e più processi significherebbero più code
indipendenti che non si vedono fra loro.

L'integrità degli archivi si verifica **prima** di accodarli, scorrendo il
flusso compresso senza estrarlo: il solo controllo del CRC dello ZIP non basta,
perché è capitato di ricevere archivi con CRC intatto e contenuto troncato
all'origine.

I duplicati si riconoscono dall'impronta **SHA-256 del contenuto**, non dal
nome: un file rinominato resta riconoscibile e l'ordine di caricamento non
conta.

## Due funzioni che escono dai dati

Oltre all'analisi, l'applicazione fa da interfaccia a due cose che altrimenti
richiederebbero di entrare sul server. Sono operazioni che spettano a chi
organizza il servizio, non a chi amministra la macchina.

**Casella** mostra l'esito della sorveglianza della casella di posta indicata
ai cittadini e consente di gestire chi riceve gli avvisi. La sorveglianza è un
programma a sé; qui se ne legge soltanto il file di stato, così l'interfaccia
non può disallineare il cursore dei messaggi già notificati.

**Dispositivi** registra i telefoni di servizio abilitati a leggere i codici di
salita, mostrando a video il codice QR di abbinamento. La logica non è
duplicata: si richiama il comando che l'applicazione di bordo già espone.

Quest'ultima merita una nota di architettura. L'applicazione gira con
`NoNewPrivileges`, quindi non può elevare i privilegi né richiamare da sola quel
comando — e quella protezione non va tolta per una funzione usata qualche volta
l'anno, visto che l'applicazione riceve caricamenti da rete. Deposita invece la
richiesta in una cartella condivisa, dove il servizio di supporto
(`deploy/tpl-dispositivi-agente.py`) la esegue e lascia la risposta:
l'applicazione non acquista nuovi poteri, ottiene una risposta. Il servizio
accetta solo le operazioni previste e ne verifica gli argomenti.

Il fondo pagina espone inoltre, a chi ne ha bisogno, il manuale
dell'applicazione, quelli per i passeggeri nelle quattro lingue, la guida per il
personale di bordo e la locandina per le fermate: si aggiornano sostituendo il
file nella cartella dei dati, senza ricompilare nulla.

## Configurazione

Tutti i parametri arrivano da variabili d'ambiente; nel codice non è scritta
alcuna credenziale. Vedi `deploy/env.esempio`.

## Licenza

**EUPL-1.2** — European Union Public Licence. Vedi [`LICENSE`](LICENSE).
