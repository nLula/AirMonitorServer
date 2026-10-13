#!/bin/sh
# Collector entrypoint: run one poll immediately, then hand over to cron.
set -e

SCHEDULE="${CRON_SCHEDULE:-*/5 * * * *}"

# cron jobs do not inherit container env vars, so bake them into a wrapper
{
  echo '#!/bin/sh'
  printenv | grep -E '^(SENSOR_API_KEY|POLL_TIMEOUT_S|SCAN_TIMEOUT_S|HISTORY_RETENTION_DAYS|DISCOVERY_RESCAN_MIN|DATA_DIR|SENSORS_CONFIG|TZ)=' | sed 's/^/export /'
  echo 'exec /usr/local/bin/python /app/poll_sensors.py'
} > /app/run_poll.sh
chmod +x /app/run_poll.sh

# >> /proc/1/fd/1 sends cron job output to the container log (docker logs)
# flock serializes cron polls with frontend-triggered ones
echo "$SCHEDULE root flock /tmp/poll.lock sh /app/run_poll.sh >> /proc/1/fd/1 2>&1" > /etc/cron.d/poller
echo "" >> /etc/cron.d/poller
chmod 0644 /etc/cron.d/poller

# Full scan on startup so a freshly plugged-in sensor appears right away
rm -f /data/poll_request
echo "[collector] Initial poll (full discovery scan)..."
FORCE_SCAN=1 sh /app/run_poll.sh || true

# Watch for poll requests dropped into the shared volume by the frontend's
# refresh button and run an immediate poll when one appears
(
  while true; do
    if [ -f /data/poll_request ]; then
      rm -f /data/poll_request
      echo "[collector] Immediate poll requested by frontend"
      flock /tmp/poll.lock sh /app/run_poll.sh || true
    fi
    sleep 2
  done
) &

echo "[collector] Cron schedule: $SCHEDULE"
exec cron -f
