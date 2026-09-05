"""Run as root after uploading backend to /opt/activity-timeline/backend."""
import pathlib
import secrets
import subprocess
import sys

IP = "47.82.104.59"


def write(path, content, mode=0o644):
    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    target.chmod(mode)


def configure():
    subprocess.run(["useradd", "--system", "--home", "/var/lib/activity-timeline", "--shell", "/usr/sbin/nologin", "activity-timeline"], check=False)
    pathlib.Path("/var/lib/activity-timeline").mkdir(exist_ok=True)
    subprocess.run(["chown", "activity-timeline:activity-timeline", "/var/lib/activity-timeline"], check=True)
    env = pathlib.Path("/etc/activity-timeline.env")
    if not env.exists():
        write(str(env), "ACTIVITYWATCH_ENV=production\nACTIVITYWATCH_TIMEZONE=Asia/Shanghai\nACTIVITYWATCH_DB_PATH=/var/lib/activity-timeline/activitywatch.db\nACTIVITYWATCH_API_TOKEN=" + secrets.token_urlsafe(48) + "\n", 0o600)
    write("/etc/systemd/system/activity-timeline.service", """[Unit]
Description=Activity Timeline API
After=network.target
[Service]
User=activity-timeline
Group=activity-timeline
WorkingDirectory=/opt/activity-timeline
EnvironmentFile=/etc/activity-timeline.env
ExecStart=/opt/activity-timeline/.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8765 --workers 1 --no-access-log
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/activity-timeline
PrivateTmp=true
[Install]
WantedBy=multi-user.target
""")
    pathlib.Path("/var/www/acme").mkdir(parents=True, exist_ok=True)
    http = """server {
    listen 80;
    server_name 47.82.104.59;
    access_log off;
    location /.well-known/acme-challenge/ { root /var/www/acme; }
    location / { return 301 https://47.82.104.59$request_uri; }
}
"""
    tls = ""
    if "--tls" in sys.argv:
        tls = """limit_req_zone $binary_remote_addr zone=activity_login:10m rate=5r/m;
server {
    listen 443 ssl;
    server_name 47.82.104.59;
    ssl_certificate /etc/letsencrypt/live/47.82.104.59/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/47.82.104.59/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 16m;
    access_log off;
    location = /api/v1/auth/login {
        limit_req zone=activity_login burst=5 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
    }
    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120s;
    }
}
"""
    write("/etc/nginx/conf.d/activity-timeline.conf", http + tls)
    if tls:
        write("/etc/systemd/system/activity-cert-renew.service", """[Unit]
Description=Renew Activity Timeline IP certificate
[Service]
Type=oneshot
ExecStart=/opt/activity-certbot/bin/certbot renew --quiet --deploy-hook "systemctl reload nginx"
""")
        write("/etc/systemd/system/activity-cert-renew.timer", """[Unit]
Description=Check IP certificate renewal twice daily
[Timer]
OnCalendar=*-*-* 00,12:00:00
RandomizedDelaySec=1800
Persistent=true
[Install]
WantedBy=timers.target
""")
    subprocess.run(["nginx", "-t"], check=True)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "activity-timeline", "nginx"], check=True)
    subprocess.run(["systemctl", "reload", "nginx"], check=True)
    if tls:
        subprocess.run(["systemctl", "enable", "--now", "activity-cert-renew.timer"], check=True)


if __name__ == "__main__":
    configure()
