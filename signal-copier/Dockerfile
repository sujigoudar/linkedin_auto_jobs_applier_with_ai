FROM python:3.11-slim

WORKDIR /app

# Base requirements only — the optional adapter dependencies (ccxt, discord.py,
# metaapi-cloud-sdk, etc.) are commented out in requirements.txt on purpose, so
# the image doesn't need every broker's SDK just to run the paper/webhook path.
# Build with e.g. `--build-arg EXTRAS="ccxt tweepy"` to add specific ones.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ARG EXTRAS=""
RUN if [ -n "$EXTRAS" ]; then pip install --no-cache-dir $EXTRAS; fi

COPY . .

# Run as a non-root, no-login user -- the image previously ran as root by
# default (a container escape or dependency RCE would then have root inside
# the container for free). /app itself and the mounted data volume both
# need to stay writable by this user (see docker-compose.yml's volumes).
RUN groupadd --system signal-copier \
    && useradd --system --gid signal-copier --home-dir /app --no-create-home --shell /usr/sbin/nologin signal-copier \
    && mkdir -p /app/data \
    && chown -R signal-copier:signal-copier /app
# /app/data exists (owned by signal-copier) before docker-compose.yml's named
# volume is ever mounted there -- Docker populates a fresh named volume from
# whatever's already at that path in the image, including ownership, so the
# volume itself ends up writable by this user too, not just /app.
USER signal-copier

# DATABASE_PATH defaults to signal_copier.db in the working directory (see
# app/config.py); docker-compose.yml mounts a volume there so it survives
# container restarts.
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
