# Linux container deployment

## Requirements

- Docker Engine 24+;
- Docker Compose v2;
- persistent storage for the `bot-data` and `bot-logs` volumes;
- a Telegram bot token and at least one ID in `ADMIN_IDS`.

## First run

1. Copy the project to the Linux host.
2. Create the environment file:

```bash
cp compose.env.example .env
chmod 600 .env
```

3. Set `BOT_TOKEN` and `ADMIN_IDS` in `.env`.
4. Build and start the container:

```bash
docker compose up -d --build
```

5. Inspect startup logs:

```bash
docker compose logs -f bot
```

The bot creates `/app/data/bot.sqlite3` on first start. The data and log directories
are Docker-managed persistent volumes and survive container replacement.

## Updating

```bash
docker compose pull
docker compose up -d --build
docker image prune -f
```

The project does not depend on the old Windows `start.bat`. Do not run
`rent_watchdog.py` or `funpay_command_watchdog.py` alongside the container: their
responsibilities are already covered by the background tasks started from `bot.py`,
and running both would duplicate FunPay scans and notifications.

## Backup

Create a SQLite backup before updates or migrations:

```bash
mkdir -p backups
docker compose exec -T bot python -c "import sqlite3; src=sqlite3.connect('/app/data/bot.sqlite3'); dst=sqlite3.connect('/tmp/bot-backup.sqlite3'); src.backup(dst); dst.close(); src.close()"
docker compose cp bot:/tmp/bot-backup.sqlite3 ./backups/bot-$(date +%Y%m%d-%H%M%S).sqlite3
```

The bot should be stopped before restoring a backup:

```bash
docker compose stop bot
docker run --rm -v funpay-bot_bot-data:/data -v "$PWD/backups:/backup" alpine sh -c 'cp /backup/FILE.sqlite3 /data/bot.sqlite3'
docker compose start bot
```

Replace `FILE.sqlite3` with the selected backup filename.

## Operations

```bash
docker compose ps
docker compose restart bot
docker compose stop bot
docker compose start bot
docker compose logs --tail=200 bot
```

The healthcheck confirms that the persistent SQLite database is initialized. It does
not call Telegram or FunPay, so temporary external API outages do not mark the
container unhealthy.

## Migration from Windows

Stop the Windows bot first, then copy its `data/bot.sqlite3` to the Linux host before
the first container start. With a named volume, copy it using a temporary container:

```bash
docker run --rm -v funpay-bot_bot-data:/data -v "$PWD:/backup" alpine sh -c 'cp /backup/bot.sqlite3 /data/bot.sqlite3'
```

Do not copy `.env`, `logs/`, or `__pycache__/` into the image. Put production secrets
only in the host `.env` file or an external secrets manager.
