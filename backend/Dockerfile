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
# A porta vem do ambiente, com 8000 como padrão. Plataforma gerenciada (Render,
# Fly, Cloud Run, Heroku) escolhe a porta e a entrega em `PORT`; uma imagem que
# ignora isso sobe, não recebe tráfego nenhum e parece saudável. `exec` para que
# o uvicorn seja o PID 1 de verdade e receba o SIGTERM do desligamento — sem
# ele, o `sh` come o sinal e a parada vira timeout.
#
# `--proxy-headers` fica no padrão porque é seguro no padrão: sem
# `FORWARDED_ALLOW_IPS`, o uvicorn confia só em 127.0.0.1, então nenhum cliente
# consegue mentir o próprio endereço. Quem põe um proxy na frente declara isso
# pela variável — e é assim que o log de auditoria registra quem agiu em vez do
# IP do proxy. O compose de produção passa o comando inteiro e não depende disto.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
