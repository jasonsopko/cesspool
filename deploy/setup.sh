#!/bin/bash
# One-time setup for cesspool.lol on node1. Run as jason: bash deploy/setup.sh
# Before running: in Cloudflare, add the cesspool.lol zone, point A/AAAA for
# cesspool.lol and www at node1 (proxied), SSL mode Full (strict), and turn
# Authenticated Origin Pulls ON. Same setup as reorg.watch.
set -euo pipefail
cd "$(dirname "$0")/.."

sudo install -d -o jason -g www-data -m 755 /var/www/cesspool.lol

# 1. HTTP only, so certbot can answer the challenge.
sudo tee /etc/nginx/sites-available/cesspool.lol >/dev/null <<'NGX'
server {
    listen 80;
    listen [::]:80;
    server_name cesspool.lol www.cesspool.lol;
    location ^~ /.well-known/acme-challenge/ { root /var/www/cesspool.lol; }
    location / { return 404; }
}
NGX
sudo ln -sf /etc/nginx/sites-available/cesspool.lol /etc/nginx/sites-enabled/cesspool.lol
sudo nginx -t && sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/www/cesspool.lol -d cesspool.lol -d www.cesspool.lol

# 2. The real config.
sudo install -m 644 deploy/cesspool.lol.nginx /etc/nginx/sites-available/cesspool.lol
sudo nginx -t && sudo systemctl reload nginx

# 3. Render the whole site once, then the minute cron.
python3 tools/update.py --out /var/www/cesspool.lol --all
( crontab -l; echo '* * * * * /usr/bin/nice -n 10 /usr/bin/python3 /home/jason/src/cesspool/tools/update.py --out /var/www/cesspool.lol >> /home/jason/cesspool.log 2>&1' ) | crontab -
curl -sI https://cesspool.lol/ | head -5
