#!/bin/bash
# Predispone la pagina "Casella" dell'applicazione e aggiorna il codice.
#
# Da eseguire con privilegi di amministratore sulla macchina che ospita
# l'applicazione:
#   sudo bash aggiorna-casella.sh
#
# Tre passaggi:
#  1. cartella di stato condivisa fra chi sorveglia la casella (operatore,
#     che scrive) e chi mostra la pagina (utente tpl, che legge);
#  2. percorso del file di stato nella configurazione dell'applicazione;
#  3. allineamento del codice e riavvio del servizio.

set -euo pipefail

STATO_DIR=/var/lib/tpl-inbox-watch
ENV_APP=/etc/tpl-navette/env
SORGENTE=/tmp/tpl-src/
DESTINAZIONE=/opt/tpl-navette/scripts/tpl_navette/

install -d -o operatore -g tpl -m 750 "${STATO_DIR}"
echo "1/3 cartella di stato: ${STATO_DIR}"

if grep -q '^TPL_INBOX_STATE_FILE=' "${ENV_APP}"; then
    sed -i "s|^TPL_INBOX_STATE_FILE=.*|TPL_INBOX_STATE_FILE=${STATO_DIR}/stato.json|" "${ENV_APP}"
    echo "2/3 configurazione: percorso aggiornato"
else
    echo "TPL_INBOX_STATE_FILE=${STATO_DIR}/stato.json" >> "${ENV_APP}"
    echo "2/3 configurazione: percorso aggiunto"
fi

rsync -a "${SORGENTE}" "${DESTINAZIONE}"
systemctl restart tpl-navette
echo "3/3 codice allineato e servizio riavviato"

systemctl is-active tpl-navette
