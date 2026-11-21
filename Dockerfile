# The scorer builds this on linux/amd64 and runs it with no network. The
# digest is the image the commits were labeled on. Keep it if the image
# runs sqlglot's tests: its Python and SQLite decide which tests run.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

# Pinned sqlglot source (allowed download: sqlglot at commit eb11aec). The static hybrid reads it to parse the
# changed files; it is not installed or executed by the selector.
ARG SQLGLOT_COMMIT=eb11aec4be8cb2122afe713a4816662c3f0fb2fa
RUN python -c "import io,tarfile,urllib.request; \
data=urllib.request.urlopen('https://codeload.github.com/tobymao/sqlglot/tar.gz/${SQLGLOT_COMMIT}', timeout=120).read(); \
tarfile.open(fileobj=io.BytesIO(data), mode='r:gz').extractall('/opt', filter='data')" \
 && mv /opt/sqlglot-${SQLGLOT_COMMIT} /opt/sqlglot \
 && test -f /opt/sqlglot/sqlglot/__init__.py

# Test dependencies for runtime probing (strategy p2): the official lock file, from PyPI only.
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock \
 && python -m compileall -q /opt/sqlglot/sqlglot /opt/sqlglot/tests

WORKDIR /app
COPY starter /app/starter
COPY blast /app/blast
COPY maps /app/maps
COPY selector.py /app/selector.py

# Build the import-time name index once (build time is not charged), then make everything world-readable.
RUN PYTHONPATH=/app python -c "from blast.hybrid_index import Index; Index('/opt/sqlglot').save('/app/maps/hybrid/index.pkl')" \
 && python -m compileall -q /app/blast /app/starter /app/selector.py \
 && chmod -R a+rX /app /opt/sqlglot

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    BLAST_SQLGLOT_SRC=/opt/sqlglot \
    SKIP_INTEGRATION=0 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    BLAST_STRATEGY=hybrid \
    BLAST_SCHEDULER=auto
CMD ["python", "-u", "/app/selector.py"]
