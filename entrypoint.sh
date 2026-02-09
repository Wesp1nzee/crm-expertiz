#!/bin/bash
set -e

cd /app

echo "--- Applying migrations ---"
python -m alembic upgrade head

echo "--- Starting uvicorn ---"

exec python -m uvicorn src.main:app --host 0.0.0.0 --port 8000
