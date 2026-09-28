#!/bin/sh
#
# Cancellazione dell'account di un cittadino su richiesta dell'interessato.
#
# Va eseguito come amministratore: internamente passa all'utenza di servizio
# dell'applicazione di bordo, l'unica che puo' leggerne la configurazione e
# quindi raggiungere il database.
#
#   sudo tpl-cancella-utente cerca --email tizio@example.org
#   sudo tpl-cancella-utente cancella --email tizio@example.org \
#        --conferma CANCELLA --prova
#
# Copyright (c) 2026 Comune di Imperia. Licenza EUPL-1.2.
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Va eseguito come amministratore: sudo tpl-cancella-utente $*" >&2
    exit 1
fi

cd "/opt/imp-qr-code/app"
exec sudo -u "impqr" "/opt/imp-qr-code/.venv/bin/python" \
    /usr/local/lib/tpl-cancella-utente.py "$@"
