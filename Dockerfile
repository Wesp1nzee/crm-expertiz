# Use a multi-stage build to keep the final image small
# First stage: Build dependencies
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

WORKDIR /app

# Install build dependencies, включая libpq-dev для psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .
COPY uv.lock .

# Sync dependencies into a new virtual environment
# Используем --frozen вместо --frozen-lockfile
RUN uv sync --frozen --no-cache

# Second stage: Runtime
FROM python:3.14-slim-bookworm

LABEL maintainer="Your Name <your.email@example.com>"
LABEL description="Production image for wesp1nzee-crm-expertiz"

# Install runtime dependencies for psycopg2 and other potential system libraries
# libpq-dev также может быть нужен в рантайме для работы psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the virtual environment from the builder stage
COPY --from=builder /app/.venv ./.venv

# Copy application source code
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini .

# Copy the entrypoint script
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Make sure scripts in .venv/bin are usable
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app/src

# Create a non-root user for security
RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /app
USER app

# Expose the port the app runs on
EXPOSE 8000

# Use the entrypoint script as the command
CMD ["/app/entrypoint.sh"]
