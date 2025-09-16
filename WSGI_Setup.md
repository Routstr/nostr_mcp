## WSGI production setup (Gunicorn + systemd)

### 1) Install and quick test

```bash
source /Users/r/projects/routstr_main/nostr_mcp/venv/bin/activate  # adjust if your venv differs
pip install --upgrade gunicorn

cd /Users/r/projects/routstr_main/nostr_mcp
gunicorn -w 1 -b 0.0.0.0:8001 http_command_server:app --timeout 120 --access-logfile -
```

- Visit `http://YOUR_IP:8001/` → should show “this is the goose den”.

### 2) Run as a systemd service

Create `/etc/systemd/system/nostr-goose.service`:

```ini
[Unit]
Description=Nostr Goose HTTP API (Gunicorn)
After=network.target

[Service]
User=user
Group=user
WorkingDirectory=/Users/r/projects/routstr_main/nostr_mcp
Environment="PATH=/Users/r/projects/routstr_main/nostr_mcp/venv/bin:/usr/bin"
ExecStart=/Users/r/projects/routstr_main/nostr_mcp/venv/bin/gunicorn -w 1 -b 127.0.0.1:8001 http_command_server:app --timeout 120 --access-logfile -
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable nostr-goose
sudo systemctl start nostr-goose
sudo systemctl status nostr-goose | cat
```

Logs:

```bash
journalctl -u nostr-goose -f
```

Note: To expose the app directly without Nginx, change `-b 127.0.0.1:8001` to `-b 0.0.0.0:8001` in `ExecStart`.

