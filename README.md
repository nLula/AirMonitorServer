# AirMonitor Server

Third part of the AirMonitor system. Two Docker containers:

| Container | What it does |
|---|---|
| `airmonitor-collector` | Cron polls every sensor's `/data` API, writes `allSensors<ts>.json`, `history.json`, `status.json` into `data/` |
| `airmonitor-frontend` | Serves the React UI (built from `../AirMonitorFrontend`) on the LAN; reads the collector's files via the shared `data/` folder |

```
sensors (ESP32, /data API)
        │  cron: CRON_SCHEDULE
        ▼
collector ──writes──► data/ ──reads── frontend ──► http://<server-ip>:8080
```

## Files produced in `data/`

- **`allSensors<YYYYMMDDHHMMSS>.json`** — what the frontend displays.
  Format: `sensors → Floor_X → sensor_N → YYYYMMDD → {co2, temperature, humidity}`.
  One value per day (latest poll wins); day history accumulates inside the file.
- **`history.json`** — every check of every sensor: timestamp, online yes/no,
  response time in ms, HTTP status, error text, readings. Pruned after
  `HISTORY_RETENTION_DAYS` (default 30).
- **`status.json`** — current state per sensor: `online`, `last_check`,
  `last_connect` (survives offline periods), `response_ms`, `error`.

## Setup

1. **Configure sensors** in [config/sensors.json](config/sensors.json).
   Sensors are matched by `sensor_id` (the `SENSOR_NUMBER` from firmware
   `config.h`) — the collector scans `discovery.subnet`, finds each sensor's
   current IP itself, and caches it in `data/known_ips.json`. If a sensor's
   IP changes (DHCP renew, reboot), the next poll rescans and heals
   automatically. No router configuration needed.
2. **Check [.env](.env)** — API key (must match firmware `config.h`), cron
   schedule, frontend port, timezone.
3. **Run:**
   ```
   docker compose up -d --build
   ```
4. **Open** `http://localhost:8080` on this machine, or
   `http://<this-pc-ip>:8080` from any device on the same wifi
   (find the IP with `ipconfig`).

## Running the server on this PC

The containers have `restart: unless-stopped`, so they come back on their own
whenever the Docker engine starts. For hands-off operation:

1. **Docker Desktop → Settings → General → enable
   "Start Docker Desktop when you sign in"** — after a reboot and login the
   whole stack is up automatically, nothing to click.
2. **Keep the PC awake**: Windows Settings → System → Power → set
   "Make my device sleep" to **Never** (when plugged in). A sleeping PC
   serves nobody.
3. That's all. Check health anytime with `docker compose ps` — both
   containers should say "running". View activity with
   `docker compose logs -f collector`.

## Runtime folder

Production runs from `C:\Users\TLL IT\Documents\AirMonitor` — a
self-contained deployment folder (prebuilt images tar, image-only
docker-compose.yml from [deploy/](deploy/), `.env`, `config/`, `data/`,
`start-airmonitor.bat`, `airmonitor.ico`). This repo is the build home:
after changing code, rebuild here, re-save the tar, copy it (and the
updated bat, if changed) into the runtime folder.

## Moving to another PC

1. Install Docker Desktop on the new machine.
2. Copy the whole runtime folder `AirMonitor` (images, config, data,
   startup script travel together).
3. Run `start-airmonitor.bat` — it starts Docker, loads the images from
   the tar automatically when missing, and brings the stack up.
4. Add the firewall rule (see below) and check `discovery.subnet` matches
   the new network. For autostart, put a shortcut to the bat into
   shell:startup.

After changing collector/frontend code, refresh the archive with:
`docker compose build; docker save -o airmonitor-images.tar airmonitor-collector:latest airmonitor-frontend:latest`

## Everyday commands

```
docker compose up -d          # start (survives reboots via restart policy)
docker compose down           # stop
docker compose logs -f collector   # watch polling live
docker compose up -d --build  # rebuild after code/frontend changes
docker compose restart collector   # re-read config/sensors.json + .env
```

Config changes (`sensors.json`, `.env`) need only a `restart`, not a rebuild.

## Adding a sensor

1. Flash firmware with a new `SENSOR_NUMBER` in `config.h` (the only
   per-sensor difference) and power it on.
2. That's it — sensors register themselves under the name their firmware
   reports, landing on `auto_add_floor` within `DISCOVERY_RESCAN_MIN`
   minutes (or immediately after `docker compose restart collector`).
   Assign the floor and a friendly alias in the frontend:
   Settings → Sensors.

## Moving to the office

1. Connect sensors to the corporate wifi (already in firmware `WIFI_SSIDS`).
2. Set `discovery.subnet` in `config/sensors.json` to the office network
   (run `ipconfig` on the server PC — e.g. IP 10.4.12.37 → subnet
   `10.4.12.0/24`), then `docker compose restart collector`.
3. If viewers can't reach the frontend from other devices, allow the port in
   Windows Firewall:
   ```
   netsh advfirewall firewall add rule name="AirMonitor" dir=in action=allow protocol=TCP localport=8080
   ```

## Notes

- The frontend container also has the old SFTP poll thread; it stays idle
  while no SFTP address is configured in the Settings dialog. Data flows
  through the shared volume instead.
- Sensor placements / PIN / aliases persist in the `frontend_config` Docker
  volume, so they survive rebuilds.




## To LAN Team:

- Device / hostname	DESKTOP-L8JF1AQ
- MAC address	10-F6-0A-69-86-E7 (Intel Wi-Fi 6E AX211 adapter)
- Request	Reserve a fixed IPv4 address for this MAC on the office wifi
- Purpose	Internal web server (AirMonitor dashboard), colleagues connect to port TCP 8080