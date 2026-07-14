# Build stage
FROM python:3.13.1-slim AS builder

WORKDIR /app

# Install build dependencies (only needed for compilation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create a virtual environment in a shared location
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements and install into venv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Runtime stage ----
FROM python:3.13.1-slim

WORKDIR /app

# Copy the virtual environment from builder (accessible by all users)
COPY --from=builder /opt/venv /opt/venv

# Copy application code
COPY . .

# Copy and set entrypoint
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Create data directory (for LLM settings file)
RUN mkdir -p /app/data

# Create non-root user for security
RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app /opt/venv
USER appuser

# Environment optimization
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/opt/venv/bin:$PATH"

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# Run migrations then start server
ENTRYPOINT ["/app/entrypoint.sh"]
