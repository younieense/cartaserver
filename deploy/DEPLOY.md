# Деплой CARTA на VPS (Docker + Nginx + HTTPS)

Схема: **браузер/Android → Nginx (443) → Docker (127.0.0.1:8443)**

TLS делает Nginx. В контейнере TLS выключен.

> **Важно:** нужен **весь** репозиторий (`web/`, `server/`, корневые `Dockerfile` и `docker-compose.yml`).  
> Если на VPS лежит только папка `server`, сайт на `/` будет пустым/404, хотя `/health` отвечает.

## 1. На VPS

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2 nginx certbot python3-certbot-nginx
sudo usermod -aG docker $USER
# перелогиньтесь
```

DNS: A-запись домена → IP VPS.

## 2. Код на сервер (полный проект)

```bash
sudo mkdir -p /opt/CARTA
# скопируйте с ПК весь проект, например:
#   scp -r ~/Desktop/CARTA/* user@VPS:/opt/CARTA/
# или git clone всего репозитория в /opt/CARTA

cd /opt/CARTA
ls
# должны быть: Dockerfile  docker-compose.yml  server/  web/  deploy/
```

Если раньше клонировали только `server` — исправьте структуру:

```bash
# вариант А: залить полный проект в /opt/CARTA (рекомендуется)
# вариант Б: рядом с server положить web и корневые файлы
```

## 3. Docker

Из **корня** `/opt/CARTA` (не из `server/`):

```bash
docker compose down
docker compose up -d --build
curl -s http://127.0.0.1:8443/health
# {"status":"ok","app":"CARTA"}

curl -sI http://127.0.0.1:8443/ | head -5
# должен быть 200 и text/html (веб-клиент)

curl -sI http://127.0.0.1:8443/js/app.js | head -3
# 200
```

Если `/` не 200 — в образ не попала папка `web`. Проверьте `ls web` в `/opt/CARTA` и пересоберите.

## 4. Nginx

```bash
sudo cp /opt/CARTA/deploy/nginx-carta.conf /etc/nginx/sites-available/carta
sudo nano /etc/nginx/sites-available/carta
# server_name nevelinko.online;
sudo ln -sf /etc/nginx/sites-available/carta /etc/nginx/sites-enabled/carta
sudo nginx -t && sudo systemctl reload nginx
```

**Обязательно** в HTTPS-блоке (его пишет certbot) должен быть отдельный `location /ws` с `Upgrade` / `Connection`. Без этого `/health` работает, а приложение «нет подключения».

Пример куска для 443:

```nginx
location /ws {
    proxy_pass http://127.0.0.1:8443;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 86400;
}
```

## 5. HTTPS

```bash
sudo certbot --nginx -d nevelinko.online
```

После certbot откройте конфиг и **проверьте**, что `location /ws` не пропал:

```bash
sudo nano /etc/nginx/sites-enabled/carta
sudo nginx -t && sudo systemctl reload nginx
```

## 6. Клиенты

| Клиент | Адрес |
|--------|--------|
| Веб | `https://nevelinko.online/` |
| Android | `wss://nevelinko.online/ws` |

## 7. Диагностика «нет подключения»

```bash
# API жив
curl -s https://nevelinko.online/health

# Есть ли веб
curl -sI https://nevelinko.online/ | head -5
curl -sI https://nevelinko.online/js/app.js | head -3

# WebSocket за Nginx (нужен websocat или браузер DevTools → Network → WS)
docker compose -f /opt/CARTA/docker-compose.yml logs --tail=50 carta

# Конфиг nginx
sudo nginx -T 2>/dev/null | grep -A20 "location /ws"
```

Типичные причины:
1. На VPS только `server/` → нет `web/` → главная страница пустая (Android это **не** блокирует).
2. В Nginx **на порту 443** нет `location /ws` с Upgrade → Android «нет подключения», `/health` при этом ок.
3. В Android указали не `wss://nevelinko.online/ws` (нужен именно `wss` и путь `/ws`).

### Почему Android не коннектится при живом /health

`/health` — обычный HTTPS-запрос.  
Приложение использует **WebSocket** (`wss://nevelinko.online/ws`).  
Certbot часто настраивает только `location /` **без** Upgrade-заголовков → WS не поднимается.

На VPS выполните:

```bash
# 1) Есть ли location /ws в рабочем конфиге?
sudo nginx -T 2>/dev/null | grep -n "location /ws" -A20

# 2) Локально контейнер принимает WS?
curl -i -N \
  -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  http://127.0.0.1:8443/ws
# ожидайте 101 Switching Protocols

# 3) То же через Nginx (снаружи / с VPS)
curl -i -N \
  -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  https://nevelinko.online/ws
# тоже 101; если 200/404/502 — править Nginx
```

Если пункт 2 = 101, а пункт 3 ≠ 101 — чините Nginx:

```bash
sudo cp /opt/CARTA/deploy/nginx-carta.conf /etc/nginx/sites-available/carta
# если SSL-пути другие — поправьте ssl_certificate* под свои
sudo nginx -t && sudo systemctl reload nginx
```

В Android: **Настройки →** `wss://nevelinko.online/ws` (без слэша в конце, схема именно `wss`).

## 8. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Порт 8443 снаружи не открывать.

## 9. Администратор (без дефолтных логинов)

```bash
cd /opt/CARTA
cp .env.example .env
nano .env
# ADMIN_LOGIN=ваш_логин
# ADMIN_PASSWORD=ваш_пароль
docker compose up -d --build
```

Если БД уже со старыми `admin`/`user`/`accountant` и хотите **чистый старт**:

```bash
docker compose down
docker volume ls | grep carta
docker volume rm <имя_volume_с_данными>
docker compose up -d --build
```

Либо оставьте данные: пропишите нового `ADMIN_*` в `.env`, перезапустите — пользователь создастся, если логина ещё нет; старых удалите в админке.

