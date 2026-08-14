FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

RUN mkdir -p /app/data

ENV SQLITE_URL=sqlite+aiosqlite:///./data/carta.db
ENV CARTA_HOST=0.0.0.0
ENV CARTA_PORT=8443
ENV CARTA_USE_TLS=0

EXPOSE 8443
CMD ["python", "-m", "app.main"]
