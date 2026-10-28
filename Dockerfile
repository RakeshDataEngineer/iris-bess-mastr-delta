FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

COPY bess_mastr ./bess_mastr
COPY config ./config
COPY sql ./sql
COPY tests ./tests
COPY scripts ./scripts
COPY pyproject.toml README.md ./

RUN useradd --create-home --uid 10001 iris \
 && mkdir -p /app/data /app/reports \
 && chown -R iris:iris /app
USER iris

ENTRYPOINT ["python", "-m", "bess_mastr"]
CMD ["--help"]
