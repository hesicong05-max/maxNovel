# Build stage
FROM m.daocloud.io/docker.io/library/python:3.13.1-slim AS builder

WORKDIR /app

# Use China mirrors for apt and pip to speed up builds on Chinese servers
RUN sed -i 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's|http://deb.debian.org/debian-security|https://mirrors.aliyun.com/debian-security|g' /etc/apt/sources.list.d/debian.sources

# Install build dependencies (only needed for compilation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create a virtual environment in a shared location
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements and install into venv using Tsinghua PyPI mirror
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# ---- Runtime stage ----
FROM m.daocloud.io/docker.io/library/python:3.13.1-slim

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
