"""
Simulated AirMonitor sensor for frontend/collector testing.

Mimics the ESP32 firmware /data endpoint (same JSON, same X-API-Key
check). Each container replica derives its sensor_id from the last octet
of its Docker network IP, so `docker compose --scale simsensor=15` yields
15 distinct sensors named sim-<id> with no per-replica config.
"""
import json
import os
import random
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_KEY = os.getenv("SENSOR_API_KEY", "")

IP = socket.gethostbyname(socket.gethostname())
SENSOR_ID = int(IP.rsplit(".", 1)[1])
SENSOR_NAME = f"sim-{SENSOR_ID}"

# Per-sensor personality so charts differ: base values + drift state
random.seed(SENSOR_ID)
state = {
    "co2": random.randrange(450, 750, 10),
    "temperature": round(random.uniform(20.5, 24.5), 1),
    "humidity": round(random.uniform(35, 55), 1),
    "latency": random.uniform(0.02, 0.25),  # simulated network/sensor delay
}


def next_reading():
    """Random walk so consecutive polls look like a live environment."""
    state["co2"] = min(1400, max(420, state["co2"] + random.choice([-20, -10, 0, 0, 10, 20])))
    state["temperature"] = min(28, max(18, round(state["temperature"] + random.uniform(-0.2, 0.2), 1)))
    state["humidity"] = min(70, max(25, round(state["humidity"] + random.uniform(-0.8, 0.8), 1)))
    return {
        "sensor_id": SENSOR_ID,
        "sensor_name": SENSOR_NAME,
        "co2": state["co2"],
        "temperature": state["temperature"],
        "humidity": state["humidity"],
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/data":
            self.send_error(404)
            return
        if self.headers.get("X-API-Key") != API_KEY:
            self._reply(401, {"error": "unauthorized"})
            return
        time.sleep(state["latency"] * random.uniform(0.6, 1.8))
        self._reply(200, next_reading())

    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # keep container logs quiet


print(f"[sim] {SENSOR_NAME} serving /data on {IP}:80", flush=True)
ThreadingHTTPServer(("0.0.0.0", 80), Handler).serve_forever()
