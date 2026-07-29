FROM python:3.12-slim

WORKDIR /app

# System deps for building wheels (pyarrow etc pull prebuilt wheels on
# linux/amd64 for py3.12, but keep build-essential for anything that doesn't).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# Editable-install target needs the package present before `pip install`.
COPY te ./te
COPY alembic ./alembic
COPY alembic.ini ./
COPY config ./config

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "te.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
