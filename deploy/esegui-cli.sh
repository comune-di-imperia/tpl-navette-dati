#!/bin/sh
# Esegue un comando dell'applicazione con il suo ambiente.
#
# Il cron non eredita le variabili che systemd passa al servizio: senza questo
# passaggio i comandi pianificati non troverebbero ne' il database ne' la posta.
#
#   esegui-cli.sh rapporto-invia --cadenza settimanale
#   esegui-cli.sh sintesi-telegram
set -eu

set -a
. /etc/tpl-navette/env
set +a

cd /opt/tpl-navette
exec .venv/bin/python -m scripts.tpl_navette.cli "$@"
