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
COPY research/ethan-p10 /app/research/ethan-p10
COPY selector.py /app/selector.py

# Build indexes and the probe preset cache once (build time is not charged).
RUN PYTHONPATH=/app python -c "from blast.hybrid_index import Index; Index('/opt/sqlglot').save('/app/maps/hybrid/index.pkl')" \
 && mkdir -p /opt/p10maps \
 && python -c "import gzip, pathlib; pathlib.Path('/opt/p10maps/covmap_line.json').write_bytes(gzip.open('/app/maps/hybrid/covmap_line.json.gz','rb').read())" \
 && cp /app/maps/hybrid/dialect_usage_map.json /app/maps/hybrid/file_reads_map.json /opt/p10maps/ \
 && cp /app/research/ethan-p10/maps/import_graph.json /opt/p10maps/ \
 && PYTHONPATH=/app BLAST_P10_ART=/opt/p10maps BLAST_SQLGLOT_SRC=/opt/sqlglot python -m blast.build_p10 \
 && python -m compileall -q /app/blast /app/starter /app/selector.py \
 && chmod -R a+rX /app /opt/sqlglot /opt/p10maps

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    BLAST_SQLGLOT_SRC=/opt/sqlglot \
    BLAST_P10_ART=/opt/p10maps \
    BR_ARTIFACTS=/opt/p10maps \
    BR_IDXCACHE=/opt/p10maps/idx_cache.pkl \
    PRESET_CACHE=/opt/p10maps/preset_cache.pkl \
    SKIP_INTEGRATION=0 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    BLAST_STRATEGY=p10 \
    BLAST_SCHEDULER=auto
CMD ["python", "-u", "/app/selector.py"]
