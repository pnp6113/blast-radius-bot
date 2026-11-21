# The scorer builds this on linux/amd64 and runs it with no network. The
# digest is the image the commits were labeled on. Keep it if the image
# runs sqlglot's tests: its Python and SQLite decide which tests run.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

WORKDIR /app
COPY starter /app/starter
COPY blast /app/blast
COPY maps /app/maps
COPY selector.py /app/selector.py

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
CMD ["python", "-u", "/app/selector.py"]
