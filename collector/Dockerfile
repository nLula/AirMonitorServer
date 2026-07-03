FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron tzdata util-linux \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir requests

WORKDIR /app
COPY poll_sensors.py entrypoint.sh ./
# Files written on Windows may carry CRLF line endings that break /bin/sh
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh

CMD ["/app/entrypoint.sh"]
