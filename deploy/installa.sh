#!/bin/bash
# Installa l'applicazione TPL navette sul VPS. Idempotente: si puo' rilanciare
# per aggiornare il codice senza toccare dati, utenti e configurazione.
#
#   sudo bash installa.sh
#
# Non tocca /etc/tpl-navette/env: al primo giro copia l'esempio e si ferma,
# perche' senza chiavi S3 il servizio non ha senso di partire.
set -euo pipefail

DESTINAZIONE=/opt/tpl-navette
DATI=/var/lib/tpl-navette
CONFIG=/etc/tpl-navette
QUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SORGENTE="$(dirname "$QUI")"

apt-get update -qq
apt-get install -y --no-install-recommends \
    python3-venv python3-dev build-essential \
    libhdf5-dev pkg-config \
    libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
    apache2 >/dev/null

id -u tpl >/dev/null 2>&1 || useradd --system --home "$DATI" --shell /usr/sbin/nologin tpl

mkdir -p "$DESTINAZIONE/scripts/tpl_navette" "$DATI/uploads" "$DATI/output" "$CONFIG" \
         /var/log/tpl-navette
chown root:adm /var/log/tpl-navette
chmod 750 /var/log/tpl-navette
touch "$DESTINAZIONE/scripts/__init__.py" "$DESTINAZIONE/scripts/tpl_navette/__init__.py"

rsync -a --delete \
    --exclude '__pycache__' --exclude 'deploy' \
    "$SORGENTE/" "$DESTINAZIONE/scripts/tpl_navette/"

if [ ! -f "$DESTINAZIONE/.venv/bin/python" ]; then
    python3 -m venv "$DESTINAZIONE/.venv"
fi
"$DESTINAZIONE/.venv/bin/pip" install --quiet --upgrade pip
"$DESTINAZIONE/.venv/bin/pip" install --quiet \
    flask gunicorn pandas tables boto3 weasyprint

chown -R root:root "$DESTINAZIONE"
chown -R tpl:tpl "$DATI"
chmod 750 "$DATI"

# Un env gia' compilato puo' essere depositato in /tmp/tpl-env: viene installato
# con i permessi giusti e subito rimosso dalla posizione temporanea.
if [ -f /tmp/tpl-env ] && [ ! -f "$CONFIG/env" ]; then
    install -o root -g tpl -m 0640 /tmp/tpl-env "$CONFIG/env"
    shred -u /tmp/tpl-env 2>/dev/null || rm -f /tmp/tpl-env
    echo "Configurazione installata da /tmp/tpl-env."
fi

if [ ! -f "$CONFIG/env" ]; then
    install -o root -g tpl -m 0640 "$QUI/env.esempio" "$CONFIG/env"
    echo
    echo "Creato $CONFIG/env dall'esempio."
    echo "Compilare TPL_SECRET_KEY, S3_ACCESS_KEY e S3_SECRET_KEY, poi rilanciare."
    exit 0
fi

install -m 0644 "$QUI/tpl-navette.service" /etc/systemd/system/tpl-navette.service

# archiviazione mensile del registro attivita' su S3
install -m 0644 "$QUI/tpl-navette-archivio.service" \
    /etc/systemd/system/tpl-navette-archivio.service
install -m 0644 "$QUI/tpl-navette-archivio.timer" \
    /etc/systemd/system/tpl-navette-archivio.timer

# conservazione dei log: 180 giorni per il sito, tetto al journal di sistema
install -m 0644 "$QUI/tpl-navette.logrotate" /etc/logrotate.d/tpl-navette
mkdir -p /etc/systemd/journald.conf.d
install -m 0644 "$QUI/journald-tpl.conf" \
    /etc/systemd/journald.conf.d/tpl-navette.conf
systemctl restart systemd-journald

systemctl daemon-reload
systemctl enable --now tpl-navette
systemctl enable --now tpl-navette-archivio.timer
systemctl restart tpl-navette

a2enmod proxy proxy_http headers ssl rewrite >/dev/null
install -m 0644 "$QUI/tpl-navette.apache.conf" /etc/apache2/sites-available/tpl-navette.conf

# Il VPS serviva gia' una pagina segnaposto sullo stesso ServerName: due vhost
# con lo stesso nome fanno vincere il primo in ordine alfabetico, quindi il
# segnaposto va disattivato. a2dissite non cancella nulla: si torna indietro
# con `a2ensite tpl.comune.imperia.it`.
for vecchio in tpl.comune.imperia.it tpl.comune.imperia.it-ssl; do
    if [ -e "/etc/apache2/sites-enabled/$vecchio.conf" ]; then
        a2dissite "$vecchio" >/dev/null
        echo "disattivato il vhost segnaposto $vecchio"
    fi
done

a2ensite tpl-navette >/dev/null
apache2ctl configtest && systemctl reload apache2

echo
echo "Servizio attivo. Senza operatori registrati l'accesso e' libero e protetto"
echo "solo dal filtro sugli IP. Per attivare le credenziali:"
echo "  cd $DESTINAZIONE && sudo -u tpl .venv/bin/python -m scripts.tpl_navette.cli utente-crea --utente <nome>"
