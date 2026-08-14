FROM python:3.12-slim

WORKDIR /app

COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/app ./app
COPY server/data ./data
COPY web ./web

RUN mkdir -p /app/data

ENV SQLITE_URL=sqlite+aiosqlite:///./data/carta.db
ENV CARTA_HOST=0.0.0.0
ENV CARTA_PORT=8443
ENV CARTA_USE_TLS=0
ENV TZ=Europe/Moscow

EXPOSE 8443
CMD ["python", "-m", "app.main"]
