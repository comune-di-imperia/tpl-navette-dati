#!/bin/sh
# Esegue un comando dell'applicazione con il suo ambiente.
#
# Il cron non eredita le variabili che systemd passa al servizio: senza questo
# passaggio i comandi pianificati non troverebbero ne' il database ne' la posta.
#
#   esegui-cli.sh rapporto-invia --cadenza settimanale
#   esegui-cli.sh sintesi-telegram
set -eu

# Cancello dell'ora locale.
#
# Il cron di Ubuntu ignora CRON_TZ — la variabile non compare nemmeno nel
# binario — quindi gli orari del crontab sono quelli del sistema, che qui tiene
# l'ora universale. I lavori si pianificano percio' su entrambe le ore
# candidate, quella dell'ora legale e quella dell'ora solare, e a decidere quale
# vale e' questo controllo: passa solo l'esecuzione che cade all'ora italiana
# voluta. Cosi' il cambio dell'ora non sposta gli invii.
if [ -n "${ORA_LOCALE:-}" ] && [ "$(TZ=Europe/Rome date +%H)" != "${ORA_LOCALE}" ]; then
    exit 0
fi

set -a
. /etc/tpl-navette/env
set +a

cd /opt/tpl-navette
exec .venv/bin/python -m scripts.tpl_navette.cli "$@"
