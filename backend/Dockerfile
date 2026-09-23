FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Sem `apt-get` de propósito. Toda dependência desta aplicação tem wheel para
# linux/amd64 — inclusive `psycopg[binary]`, que já embute a libpq, e
# `cryptography`, que embute o OpenSSL. Instalar `build-essential` aqui custava
# ~200 MB na imagem e, pior, fazia cada build depender de um mirror Debian: um
# `deb.debian.org` fora do ar, bloqueado por firewall corporativo ou lento
# transformava "atualizar a aplicação" em "não sobe hoje". Se algum dia entrar
# uma dependência sem wheel, o build falha aqui, em voz alta — e aí a decisão é
# consciente, não herdada.
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir -e .

COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
