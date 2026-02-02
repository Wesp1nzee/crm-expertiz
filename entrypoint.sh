#!/bin/bash
set -e # Exit immediately if a command exits with a non-zero status

echo "Applying database migrations..."
# This command reads DB_URL from the environment variable set in docker-compose.yml
uv run alembic upgrade head

echo "Migrations applied successfully."

echo "Starting the application..."
exec gunicorn --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 src.main:app
