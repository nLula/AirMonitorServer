"""
One poll cycle over all sensors. Executed by cron inside the collector
container (see entrypoint.sh) and once at container startup.

Sensors register themselves: the collector scans the discovery subnet, and
every device answering /data with a sensor_id (SENSOR_NUMBER in firmware) is
tracked automatically - sensor_id N becomes slot sensor_<N+1> on the default
floor. Entries in config/sensors.json are optional overrides to pin a sensor
to a specific floor/slot/name (or a fixed url, skipping discovery).

Discovered IPs are cached in /data/known_ips.json; a full rescan happens at
container start, whenever a cached sensor stops answering, and at least every
DISCOVERY_RESCAN_MIN minutes so new sensors show up on their own.

Reads:  /app/config/sensors.json      discovery settings + optional overrides
Writes: /data/allSensors<YYYYMMDDHHMMSS>.json   frontend data file
        /data/history.json            every check ever made (pruned by age)
        /data/status.json             current per-sensor state incl. last_connect
        /data/known_ips.json          sensor_id -> last known URL + scan meta
"""

import ipaddress
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import requests

CONFIG_FILE = Path(os.getenv("SENSORS_CONFIG", "/app/config/sensors.json"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
API_KEY = os.getenv("SENSOR_API_KEY", "")
TIMEOUT_S = float(os.getenv("POLL_TIMEOUT_S", "5"))
SCAN_TIMEOUT_S = float(os.getenv("SCAN_TIMEOUT_S", "2"))
HISTORY_RETENTION_DAYS = int(os.getenv("HISTORY_RETENTION_DAYS", "30"))
DISCOVERY_RESCAN_MIN = int(os.getenv("DISCOVERY_RESCAN_MIN", "60"))
FORCE_SCAN = os.getenv("FORCE_SCAN", "") == "1"

HISTORY_FILE = DATA_DIR / "history.json"
STATUS_FILE = DATA_DIR / "status.json"
KNOWN_IPS_FILE = DATA_DIR / "known_ips.json"
# Floor assignments made in the frontend (Settings > Sensors), written by
# the frontend backend into the shared volume: {"<sensor_id>": {"floor": ...}}
ASSIGNMENTS_FILE = DATA_DIR / "config" / "sensor_assignments.json"

# Same naming scheme the frontend backend looks for in its Library folder
SENSOR_FILE_RE = re.compile(r"^allSensors(\d{14})\.json$")


def log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}", flush=True)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path, data) -> None:
    """Write via temp file + rename so the frontend never reads a half-written file."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def check_sensor(url: str, timeout: float) -> dict:
    """Hit one sensor's /data endpoint. Returns a check record."""
    record = {
        "online": False,
        "response_ms": None,
        "http_status": None,
        "error": None,
        "data": None,
    }
    start = time.monotonic()
    try:
        r = requests.get(url, headers={"X-API-Key": API_KEY}, timeout=timeout)
        record["response_ms"] = int((time.monotonic() - start) * 1000)
        record["http_status"] = r.status_code
        r.raise_for_status()
        record["data"] = r.json()
        record["online"] = True
    except requests.exceptions.Timeout:
        record["response_ms"] = int((time.monotonic() - start) * 1000)
        record["error"] = f"timeout after {timeout}s"
    except requests.exceptions.ConnectionError:
        record["error"] = "unreachable (connection error)"
    except requests.exceptions.HTTPError as e:
        record["error"] = f"HTTP {r.status_code}: {e}"
    except ValueError:
        record["error"] = "invalid JSON in response"
    except Exception as e:  # anything else should never kill the whole cycle
        record["error"] = str(e)
    return record


def discover_sensors(subnet: str, port: int) -> dict:
    """
    Probe every host in the subnet for a sensor /data endpoint.
    Returns {sensor_id (str): {"url": ..., "result": check record}}.
    """
    hosts = [str(h) for h in ipaddress.ip_network(subnet, strict=False).hosts()]
    log(f"Scanning {subnet} ({len(hosts)} hosts) for sensors...")
    start = time.monotonic()

    def probe(ip: str):
        url = f"http://{ip}/data" if port == 80 else f"http://{ip}:{port}/data"
        r = check_sensor(url, SCAN_TIMEOUT_S)
        if r["online"] and isinstance(r["data"], dict) and "sensor_id" in r["data"]:
            return url, r
        return None

    found = {}
    with ThreadPoolExecutor(max_workers=64) as ex:
        for res in ex.map(probe, hosts):
            if res:
                url, r = res
                sid = str(r["data"]["sensor_id"])
                found[sid] = {"url": url, "result": r}
                log(f"  found sensor_id={sid} ({r['data'].get('sensor_name', '?')}) at {url}")

    log(f"Scan finished in {time.monotonic() - start:.1f}s, {len(found)} sensor(s) found")
    return found


def load_known() -> tuple[dict, str | None]:
    """
    Return ({sensor_id: {"url": ..., "name": ...}}, last_full_scan iso or None).
    Upgrades older cache formats (flat, or url-only values) transparently.
    """
    raw = load_json(KNOWN_IPS_FILE, {})
    if "sensors" in raw or "_meta" in raw:
        entries = dict(raw.get("sensors", {}))
        last_scan = raw.get("_meta", {}).get("last_full_scan")
    else:
        entries = {k: v for k, v in raw.items() if not k.startswith("_")}
        last_scan = None
    for sid, v in entries.items():
        if isinstance(v, str):  # old format: value was just the URL
            entries[sid] = {"url": v, "name": f"sensor-{sid}"}
    return entries, last_scan


def save_known(known: dict, last_full_scan: str | None) -> None:
    write_json_atomic(KNOWN_IPS_FILE, {
        "_meta": {"last_full_scan": last_full_scan},
        "sensors": known,
    })


def find_latest_allsensors() -> tuple[str | None, Path | None]:
    candidates = []
    for f in DATA_DIR.iterdir():
        m = SENSOR_FILE_RE.match(f.name)
        if m:
            candidates.append((m.group(1), f))
    if not candidates:
        return None, None
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0]


def update_allsensors(readings: list[dict], registry: list[dict], now: datetime) -> None:
    """
    Merge today's readings into the newest allSensors file and write a new
    timestamped one (frontend format: sensors -> Floor_X -> sensor_N -> YYYYMMDD).
    Older allSensors files are removed afterwards - day history lives inside
    the file itself, check history lives in history.json.
    """
    _, latest_path = find_latest_allsensors()
    doc = load_json(latest_path, {}) if latest_path else {}
    sensors = doc.setdefault("sensors", {})
    sensors["lastchanged"] = now.isoformat()

    # When a sensor was reassigned to another floor, move its accumulated
    # day history there so charts and placements follow the sensor.
    owned = {(s["floor"], s["slot"]) for s in registry}
    for s in registry:
        for floor_key in [k for k in sensors if k != "lastchanged"]:
            if floor_key != s["floor"] and s["slot"] in sensors.get(floor_key, {}) \
                    and (floor_key, s["slot"]) not in owned:
                old_days = sensors[floor_key].pop(s["slot"])
                merged = {**old_days, **sensors.setdefault(s["floor"], {}).get(s["slot"], {})}
                sensors[s["floor"]][s["slot"]] = merged
                if not sensors[floor_key]:
                    del sensors[floor_key]
                log(f"Moved {s['slot']} history: {floor_key} -> {s['floor']}")

    day_key = now.strftime("%Y%m%d")
    for r in readings:
        data = r["data"]
        floor = sensors.setdefault(r["floor"], {})
        slot = floor.setdefault(r["slot"], {})
        # Values as strings to match the format the frontend already consumes
        slot[day_key] = {
            "co2": str(int(round(float(data.get("co2", 0))))),
            "temperature": f"{float(data.get('temperature', 0)):.1f}",
            "humidity": f"{float(data.get('humidity', 0)):.1f}",
        }

    new_path = DATA_DIR / f"allSensors{now:%Y%m%d%H%M%S}.json"
    write_json_atomic(new_path, doc)

    for f in DATA_DIR.iterdir():
        if SENSOR_FILE_RE.match(f.name) and f != new_path:
            f.unlink(missing_ok=True)

    log(f"Wrote {new_path.name} ({len(readings)} sensor(s) reporting)")


def update_history(checks: list[dict], now: datetime) -> None:
    history = load_json(HISTORY_FILE, {"checks": []})
    history["checks"].extend(checks)

    cutoff = (now - timedelta(days=HISTORY_RETENTION_DAYS)).isoformat()
    history["checks"] = [c for c in history["checks"] if c["timestamp"] >= cutoff]

    write_json_atomic(HISTORY_FILE, history)


def update_status(checks: list[dict], now: datetime) -> None:
    # Rebuilt from scratch each cycle so sensors removed from the registry
    # disappear; last_connect is carried over from the previous state.
    old = load_json(STATUS_FILE, {}).get("sensors", {})
    status = {"last_poll": now.isoformat(), "sensors": {}}

    for c in checks:
        key = f"{c['floor']}/{c['slot']}"
        prev = old.get(key, {})
        status["sensors"][key] = {
            "name": c["name"],
            "sensor_id": c.get("sensor_id"),
            "url": c["url"],
            "online": c["online"],
            "last_check": c["timestamp"],
            # last_connect survives offline periods - only updated on success
            "last_connect": c["timestamp"] if c["online"] else prev.get("last_connect"),
            "response_ms": c["response_ms"],
            "error": c["error"],
        }

    write_json_atomic(STATUS_FILE, status)


def matches_id(result: dict, sid: str) -> bool:
    """True if a successful check came from the sensor we expected."""
    return (
        result["online"]
        and isinstance(result["data"], dict)
        and str(result["data"].get("sensor_id")) == sid
    )


def build_registry(config: dict, known: dict) -> list[dict]:
    """
    Combine config overrides with auto-discovered sensors into one list of
    {sid?, url?, name, floor, slot}. Auto sensors keep the name their
    firmware reports and use it directly as their key in the data file;
    the floor comes from the frontend assignment (sensor_assignments.json)
    or auto_add_floor when unassigned. config/sensors.json entries win.
    """
    discovery = config.get("discovery") or {}
    default_floor = discovery.get("auto_add_floor", "Floor_10")
    overrides = config.get("sensors") or []
    assignments = load_json(ASSIGNMENTS_FILE, {})

    registry = []
    configured_ids = set()
    for s in overrides:
        if "sensor_id" in s:
            configured_ids.add(str(s["sensor_id"]))
        registry.append(s)

    if discovery.get("auto_add", True):
        for sid in sorted(known, key=lambda x: int(x) if x.isdigit() else 0):
            if sid not in configured_ids:
                name = known[sid].get("name") or f"sensor-{sid}"
                registry.append({
                    "sensor_id": int(sid) if sid.isdigit() else sid,
                    "name": name,
                    "floor": assignments.get(sid, {}).get("floor", default_floor),
                    "slot": name,
                    "auto": True,
                })
    return registry


def main() -> None:
    if not API_KEY:
        log("WARNING: SENSOR_API_KEY is empty - sensors will reject requests")

    config = load_json(CONFIG_FILE, None)
    if config is None:
        log(f"ERROR: cannot read {CONFIG_FILE}")
        sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    known, last_scan = load_known()
    discovery = config.get("discovery") or {}
    now = datetime.now()

    # ── Pass 1: try every known sensor at its fixed or cached URL ──
    registry = build_registry(config, known)
    outcomes = {}  # registry index -> (url, result) or None
    pending = []   # indices that need a discovery scan
    for i, s in enumerate(registry):
        if "url" in s:
            outcomes[i] = (s["url"], check_sensor(s["url"], TIMEOUT_S))
            continue
        sid = str(s["sensor_id"])
        url = known.get(sid, {}).get("url")
        if url:
            result = check_sensor(url, TIMEOUT_S)
            if matches_id(result, sid):
                outcomes[i] = (url, result)
                # keep the cached name in sync with what the firmware reports
                live_name = result["data"].get("sensor_name")
                if live_name:
                    known[sid]["name"] = live_name
                continue
            log(f"{s.get('name', sid)} no longer answering at {url}, will rescan")
        outcomes[i] = None
        pending.append(i)

    # ── Pass 2: full scan when something is missing, on schedule, or forced ──
    rescan_due = (
        last_scan is None
        or datetime.fromisoformat(last_scan) < now - timedelta(minutes=DISCOVERY_RESCAN_MIN)
    )
    if discovery.get("subnet") and (pending or rescan_due or FORCE_SCAN):
        found = discover_sensors(discovery["subnet"], int(discovery.get("port", 80)))
        last_scan = now.isoformat()
        for sid, hit in found.items():
            known[sid] = {
                "url": hit["url"],
                "name": hit["result"]["data"].get("sensor_name") or f"sensor-{sid}",
            }
        # resolve sensors that were pending before the scan
        for i in pending:
            sid = str(registry[i]["sensor_id"])
            if sid in found:
                outcomes[i] = (found[sid]["url"], found[sid]["result"])
        # register sensors seen for the very first time
        registry_ids = {str(s["sensor_id"]) for s in registry if "sensor_id" in s}
        new_ids = [sid for sid in found if sid not in registry_ids]
        if new_ids:
            registry = build_registry(config, known)
            for i, s in enumerate(registry):
                if str(s.get("sensor_id")) in new_ids:
                    sid = str(s["sensor_id"])
                    outcomes[i] = (found[sid]["url"], found[sid]["result"])
                    log(f"NEW sensor auto-registered: {s['name']} -> {s['floor']}/{s['slot']}")
                elif i not in outcomes:
                    outcomes[i] = None
    elif pending and not discovery.get("subnet"):
        log("WARNING: sensors unreachable and no discovery.subnet configured")

    save_known(known, last_scan)

    # ── Record results ──
    checks = []       # every attempt -> history.json + status.json
    readings = []     # successful ones -> allSensors file
    for i, s in enumerate(registry):
        name = s.get("name", s.get("url") or f"sensor_id={s.get('sensor_id')}")
        if outcomes.get(i) is None:
            url, result = None, {
                "online": False, "response_ms": None, "http_status": None,
                "error": "not found on network (discovery scan)", "data": None,
            }
        else:
            url, result = outcomes[i]
        # prefer the name the firmware reports for auto-registered sensors
        if s.get("auto") and result["online"]:
            name = result["data"].get("sensor_name", name)
        check = {
            "timestamp": now.isoformat(),
            "name": name,
            "sensor_id": s.get("sensor_id"),
            "floor": s["floor"],
            "slot": s["slot"],
            "url": url,
            "online": result["online"],
            "response_ms": result["response_ms"],
            "http_status": result["http_status"],
            "error": result["error"],
        }
        if result["online"]:
            check.update(
                co2=result["data"].get("co2"),
                temperature=result["data"].get("temperature"),
                humidity=result["data"].get("humidity"),
            )
            readings.append({**check, "data": result["data"]})
            log(f"OK      {name} ({check['response_ms']} ms)  "
                f"CO2 {check['co2']} ppm, {check['temperature']} C, {check['humidity']} %")
        else:
            log(f"OFFLINE {name}: {check['error']}")
        checks.append(check)

    if not checks:
        log("No sensors known yet and none found on the network")
        return

    update_history(checks, now)
    update_status(checks, now)
    if readings:
        update_allsensors(readings, registry, now)
    else:
        log("No sensor reachable - allSensors file left unchanged")


if __name__ == "__main__":
    main()
