# Builds the AirMonitorFrontend project into a self-contained image.
# "frontend_src" is an additional build context pointing at ../AirMonitorFrontend
# (see docker-compose.yml) - the frontend repo itself stays untouched.

# ── Stage 1: compile the React app ───────────────────────
FROM node:22-alpine AS webbuild
WORKDIR /build
COPY --from=frontend_src frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY --from=frontend_src frontend/ ./
RUN npm run build

# ── Stage 2: Flask runtime ────────────────────────────────
FROM python:3.12-slim
WORKDIR /app

RUN pip install --no-cache-dir flask asyncssh waitress

COPY --from=frontend_src backend.py paths.py ./
COPY --from=webbuild /build/dist ./frontend/dist
COPY run_server.py ./

EXPOSE 5000
CMD ["python", "run_server.py"]
