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

# DATABASE_PATH defaults to signal_copier.db in the working directory (see
# app/config.py); docker-compose.yml mounts a volume there so it survives
# container restarts.
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
