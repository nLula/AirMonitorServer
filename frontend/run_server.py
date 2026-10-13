"""
Container entrypoint for the AirMonitor frontend.

backend.py's own __main__ block rebuilds the React app with npm on every
start - in Docker the build already happened in the image, so this launcher
just starts the Flask app (via waitress, which handles concurrent LAN
viewers better than the dev server).

Sensor data arrives through the shared /app/Library volume, written by the
collector container - the legacy SFTP poll thread is not started.
"""

from waitress import serve

from backend import app

if __name__ == "__main__":
    print("AirMonitor frontend listening on 0.0.0.0:5000")
    serve(app, host="0.0.0.0", port=5000)
