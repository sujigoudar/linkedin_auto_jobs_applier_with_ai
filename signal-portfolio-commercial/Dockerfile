FROM python:3.11-slim

WORKDIR /app

# The pure cross-service contract package (signal_platform_contracts) is
# an editable sibling dependency (see requirements.txt's own `-e ../
# signal_platform_contracts`) -- copied in BEFORE requirements.txt's own
# pip install so that install step can actually resolve it.
COPY signal_platform_contracts /signal_platform_contracts

COPY signal-portfolio-commercial/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY signal-portfolio-commercial/ .

# Run as a non-root, no-login user -- same reasoning as signal-copier's
# own Dockerfile: a container escape or dependency RCE gets no root
# inside the container for free.
RUN groupadd --system commercial \
    && useradd --system --gid commercial --home-dir /app --no-create-home --shell /usr/sbin/nologin commercial \
    && chown -R commercial:commercial /app
USER commercial

# ops/bootstrap.py imports `app.*` (`from app import config`) -- its own
# script directory (ops/) is what Python puts on sys.path[0] by
# default, not /app itself, so PYTHONPATH must include /app for that
# import to resolve when the entrypoint invokes it as a plain script.
ENV PYTHONPATH=/app

EXPOSE 8001

# `deploy/entrypoint-commercial.sh` runs the real migration chain +
# ops/bootstrap.py's own idempotent role/tenant/stream provisioning
# BEFORE starting uvicorn -- see that script's own comments for why
# this can't just be `CMD ["uvicorn", ...]` directly.
ENTRYPOINT ["/app/deploy/entrypoint-commercial.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
