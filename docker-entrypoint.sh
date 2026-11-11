#!/bin/sh
# Apply migrations, then serve. Traefik (Dokploy) terminates TLS in front.
set -e
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
