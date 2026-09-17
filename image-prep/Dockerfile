FROM python:3.12-slim

# libwebp + libjpeg are the only system deps Pillow needs beyond what's
# already in python:3.12-slim. Install via apt, then drop apt lists for size.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libjpeg62-turbo \
        libwebp7 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user — service binds in-network only, no need to run as root.
RUN useradd --create-home --shell /usr/sbin/nologin --uid 1000 imageprep

WORKDIR /app

# Install Python deps before copying source so docker layer cache hits on rebuilds.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# App code + bundled fonts (Space Grotesk variable TTF for the /header endpoint,
# design lock: tcs-scripts#5). OFL.txt travels with the font for license compliance.
COPY app.py /app/app.py
COPY fonts/ /app/fonts/

# Output volume mount point for /header's generated JPEGs. Owner must match the
# runtime user so writes succeed once a host bind-mount is attached.
RUN mkdir -p /output \
    && chown -R imageprep:imageprep /output /app/fonts

USER imageprep
EXPOSE 3001

# Healthcheck wired into Docker so docker ps and compose show liveness.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3001/healthz', timeout=2)"

CMD ["gunicorn", "--bind", "0.0.0.0:3001", "--workers", "1", "--threads", "2", \
     "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
