# Gestione dati navette a guida autonoma

Applicazione del **Comune di Imperia** per la raccolta, l'analisi e la
conservazione dei dati di funzionamento delle navette a guida autonoma in
sperimentazione sul territorio comunale.

In esercizio su <https://tpl.comune.imperia.it>.

## A cosa serve

Ogni giornata di esercizio produce due archivi per navetta, uno per computer di
bordo. L'applicazione li riceve, ne verifica l'integrità, ne ricava le grandezze
di interesse, produce un report in PDF e conserva tutto in modo permanente.

I dati originali non vengono mai modificati: restano disponibili per qualunque
rielaborazione futura.

## Cosa calcola

L'estrattore di bordo fornisce posizione, assetto e telemetria del veicolo, ma
non le grandezze cinematiche. Vengono quindi derivate:

| Grandezza | Come |
|---|---|
| **Accelerazione** | dalla velocità misurata a bordo, non dalla posizione, che sarebbe troppo rumorosa |
| **Regime di rotazione** | dalla velocità e dalla geometria della ruota |
| **Beccheggio** | dall'assetto del computer di localizzazione |

La **ripartizione fra guida autonoma e manuale** è pesata sul tempo effettivo di
marcia e sulla percorrenza, non sul numero di rilevazioni: il veicolo resta
fermo per gran parte della giornata, e contare le rilevazioni misurerebbe
soprattutto in quale modalità è stato parcheggiato.

Controprova sui totali: la distanza ricavata dalla posizione e quella letta
dall'odometro di bordo concordano entro l'1,5%.

## Struttura

| Modulo | Ruolo |
|---|---|
| `app.py` | applicazione web: accesso, caricamento, coda di elaborazione |
| `analisi.py` | lettura degli archivi e calcolo delle grandezze |
| `pipeline.py` | orchestrazione: analisi, archiviazione, report |
| `referto.py` | composizione del report PDF |
| `esplora.py` | consultazione dell'archivio |
| `db.py` | persistenza: utenze, ruoli, elaborazioni, registro |
| `permessi.py` | matrice dei permessi per ruolo |
| `manuale.py` | manuale d'uso in italiano e inglese |
| `deploy/` | unit systemd, vhost, rotazione dei log, installazione |
| `collaudi/` | verifiche automatiche |

## Ruoli

| | Amministratore | Tecnico | Consultazione |
|---|:---:|:---:|:---:|
| Consultare dati e archivio | ✔ | ✔ | ✔ |
| Scaricare i report | ✔ | ✔ | ✔ |
| Scaricare i file archiviati | ✔ | ✔ | ✘ |
| Caricare i dati | ✔ | ✔ | ✘ |
| Leggere il registro | ✔ | ✘ | ✘ |
| Gestire le utenze | ✔ | ✘ | ✘ |

## Installazione

```
sudo bash deploy/installa.sh
```

Installa dipendenze, ambiente virtuale, servizio e host virtuale. Alla prima
esecuzione crea `/etc/tpl-navette/env` dal modello: vanno compilate le chiavi
di archiviazione e i parametri di posta prima di rilanciare.

## Manuale d'uso

- [Italiano](https://tpl.comune.imperia.it/manuale/it.pdf)
- [English](https://tpl.comune.imperia.it/manuale/en.pdf)

## Licenza

[EUPL-1.2](LICENSE.txt) — Copyright © Comune di Imperia.

---

Software Architect: Ing. Carlo Capacci · Software Coder: Claude.ai
