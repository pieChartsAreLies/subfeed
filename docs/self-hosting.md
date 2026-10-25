# Self-hosting the Subfeed service

This page sets up the server half of Subfeed on a Linux machine you leave running: a Debian or Ubuntu VM, a Proxmox LXC container, a mini PC, a Raspberry Pi 5. Examples use Debian 13 commands. Placeholders you replace are written in capitals: `YOUR_SERVER_IP`, `YOUR_TOKEN`.

The service needs about 1 GB of RAM and a few hundred MB of disk. Saved videos need more (see [Saved videos](#saved-videos-optional)).

## What runs where

| Piece | Where | Notes |
|---|---|---|
| Service code | `/opt/subfeed/service` | the repo's `service/` directory |
| Python venv | `/opt/subfeed/venv` | FastAPI, uvicorn, httpx, yt-dlp |
| Database | `/var/lib/subfeed/subfeed.db` | SQLite, local disk |
| Google token | `/var/lib/subfeed/google-token.json` | written after you sign in on the TV |
| Env file | `/etc/subfeed/subfeed.env` | secrets, `root:subfeed` mode 0640 |
| Saved videos | `/srv/saved` | optional, must be a mount point |
| Caddy | `/etc/caddy/Caddyfile` | HTTPS front on port 443 |
| Units | `/etc/systemd/system/subfeed*.{service,timer}` | from `infra/systemd/` |

## 1. Packages

```bash
apt-get update
apt-get install -y python3 python3-venv python3-pip ffmpeg git curl ca-certificates unzip sqlite3 caddy
```

Python 3.12 or newer is required. ffmpeg is used for saving videos and for remuxing.

### deno

yt-dlp needs a JavaScript runtime to extract YouTube streams. Without one it logs `No supported JavaScript runtime could be found` and some formats go missing. deno is the runtime yt-dlp enables by default, so installing it to `/usr/local/bin` (which the systemd unit puts on `PATH`) is enough.

```bash
tag=$(curl -fsSL -o /dev/null -w '%{url_effective}' https://github.com/denoland/deno/releases/latest | sed 's@.*/@@')
curl -fsSLO https://github.com/denoland/deno/releases/download/$tag/deno-x86_64-unknown-linux-gnu.zip
curl -fsSLO https://github.com/denoland/deno/releases/download/$tag/deno-x86_64-unknown-linux-gnu.zip.sha256sum
sha256sum -c deno-x86_64-unknown-linux-gnu.zip.sha256sum
unzip deno-x86_64-unknown-linux-gnu.zip && install -m 0755 deno /usr/local/bin/deno
```

On an ARM machine use the `aarch64` zip instead.

## 2. Service user and code

```bash
groupadd --system subfeed
useradd --system -g subfeed -m -d /opt/subfeed -s /bin/bash subfeed
install -d -o subfeed -g subfeed -m 0750 /var/lib/subfeed
install -d -m 0750 -g subfeed /etc/subfeed

git clone https://github.com/pieChartsAreLies/subfeed.git /tmp/subfeed-src
cp -a /tmp/subfeed-src/service /opt/subfeed/service
chown -R subfeed:subfeed /opt/subfeed
su subfeed -c 'python3 -m venv /opt/subfeed/venv && /opt/subfeed/venv/bin/pip install -U pip && /opt/subfeed/venv/bin/pip install -e /opt/subfeed/service'
```

(Replace the clone URL with wherever you got the repo.) Check it installed:

```bash
su subfeed -c '/opt/subfeed/venv/bin/yt-dlp --version && /opt/subfeed/venv/bin/subfeed --help'
```

## 3. The env file

Create `/etc/subfeed/subfeed.env`, owned `root:subfeed`, mode `0640`. Make the token with `openssl rand -hex 32`.

```bash
# Required. The TV app sends this on every request. It is compiled into the APK.
SUBFEED_API_TOKEN=YOUR_TOKEN

# Google sign-in (see docs/google-setup.md). Without these, the Settings screen says
# sign-in is not set up, and Subfeed shows only the channels you seeded by hand.
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# Optional. Lets Subfeed sort new channels into tabs with an LLM (via OpenRouter).
# Without it, new channels land in "Other" and you move them by hand from the TV.
OPENROUTER_API_KEY=
```

Every variable the service reads:

| Variable | Default | Meaning |
|---|---|---|
| `SUBFEED_API_TOKEN` | none (required) | Bearer token. If it is empty, every authenticated route refuses requests. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | none | OAuth client of type "TVs and Limited Input devices". |
| `OPENROUTER_API_KEY` | none | Enables automatic categorization. The model name is set in `service/subfeed/categorize.py`. |
| `SUBFEED_DB` | `/var/lib/subfeed/subfeed.db` | SQLite path. The unit file sets it explicitly. |
| `SUBFEED_SAVED_DIR` | `/srv/saved` | Where saved videos and database backups go. Must be a mount point. |
| `SUBFEED_GOOGLE_TOKEN` | `/var/lib/subfeed/google-token.json` | Where the Google refresh token is stored. |
| `SUBFEED_SCHEDULER` | `1` | `0` turns off background polling and syncing (the unit file sets `1`). |
| `SUBFEED_SAVE_WORKER` | `1` | `0` turns off the save-to-disk worker. |
| `SUBFEED_CANARY` | `1` | `0` turns off the hourly check that a known public video still extracts. |

The env file is read by systemd. Do not put quotes around values.

## 4. Seed channels (optional)

Once you sign in with Google, Subfeed pulls your real subscriptions and a seed is unnecessary. To start with a hand-picked list before signing in:

```bash
cp /opt/subfeed/service/seed/channels.example.txt /opt/subfeed/service/seed/channels.txt
# edit it: one channel per line, "<UC channel id> <@handle or -> <name>"
su subfeed -c 'cd /opt/subfeed/service && SUBFEED_DB=/var/lib/subfeed/subfeed.db /opt/subfeed/venv/bin/subfeed seed'
```

`subfeed seed` uses `seed/channels.txt` if it exists and the example file otherwise. When you later sign in with Google, channels you do not actually subscribe to are hidden (their saved videos stay).

## 5. systemd

```bash
cp /tmp/subfeed-src/infra/systemd/subfeed.service /etc/systemd/system/
cp /tmp/subfeed-src/infra/systemd/subfeed-backup.service /etc/systemd/system/
cp /tmp/subfeed-src/infra/systemd/subfeed-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now subfeed.service
```

The service listens on `127.0.0.1:8000`. Caddy is the only thing that talks to it. Check it:

```bash
curl -s http://127.0.0.1:8000/api/health
set -a; . /etc/subfeed/subfeed.env; set +a
curl -s -H "Authorization: Bearer $SUBFEED_API_TOKEN" http://127.0.0.1:8000/api/feed | head -c 300
```

`/api/health` is the one route that needs no token. It reports sync times, the yt-dlp version and error flags (no secrets, no video titles) and feeds the status lines on the TV's Settings screen. Every other `/api` and `/media` route needs the bearer token. Logs: `journalctl -u subfeed -f`.

## 6. Caddy (HTTPS)

The TV app talks HTTPS only and trusts one certificate authority: Caddy's own local CA, via `tls internal`. No public domain or Let's Encrypt is involved.

Give the server a fixed LAN address (a DHCP reservation or a static IP), then:

```bash
sed 's/YOUR_SERVER_IP/192.0.2.10/g' /tmp/subfeed-src/infra/caddy/Caddyfile > /etc/caddy/Caddyfile   # use your real IP
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl reload caddy
```

The template serves both `https://YOUR_SERVER_IP` and `https://subfeed.lan`. Delete the hostname if you do not have local DNS for it. The TV app talks to whatever you give it as `SUBFEED_API_BASE`, and the certificate has to cover that name or address.

Caddy creates its CA the first time it serves. The root certificate you will give to the APK build is:

```
/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt
```

Copy that file to the machine where you build the APK. It is a public certificate (no private key), but treat it as yours: anything that holds it plus a matching key could impersonate your server to the app, and the key (`root.key`) stays on the server.

Check from another machine on the LAN:

```bash
curl --cacert root.crt -s -o /dev/null -w '%{http_code}\n' https://YOUR_SERVER_IP/api/feed   # 401 without a token
curl --cacert root.crt -s -H "Authorization: Bearer YOUR_TOKEN" https://YOUR_SERVER_IP/api/health
```

If you rebuild the server, Caddy generates a new root and you have to rebuild the APK with it, unless you restore `/var/lib/caddy` from backup.

## 7. Saved videos (optional)

"Save to NAS" in the long-press menu downloads the video as an MP4 into `SUBFEED_SAVED_DIR` (default `/srv/saved`), with its thumbnail, captions and metadata beside it. Saved videos are kept even if YouTube later removes them.

The service refuses to write there unless the path is a real mount point. This is deliberate: if your NAS is down, an empty directory on the server's own disk would otherwise quietly fill the root filesystem. Any mount works: NFS, SMB, a second disk, or a bind mount of a local directory.

NFS example (`/etc/fstab`), using a soft mount so a dead NAS returns errors instead of hanging the service:

```
YOUR_NAS_IP:/path/to/export /srv/saved nfs nosuid,noexec,nodev,softerr,timeo=50,retrans=3,_netdev,nofail,x-systemd.automount,x-systemd.mount-timeout=60 0 0
```

Local directory instead of a NAS:

```bash
mkdir -p /srv/saved /srv/saved-data
chown subfeed:subfeed /srv/saved-data
echo '/srv/saved-data /srv/saved none bind 0 0' >> /etc/fstab
mount /srv/saved
```

The `subfeed` user needs write access to the mount. With NFS that means the numeric uid of `subfeed` on the server matches an owner on the export. Quota: the Settings screen reports "Saved videos use X GB of Y GB" from the mount's free space, and a save is refused when it would leave under 2 GB free.

If the service runs in an unprivileged LXC container, the container's uid is offset on the host (container uid 1500 appears as 101500 on the host and the NAS); set the export's owner to that number.

### Nightly database backup

`subfeed-backup.timer` runs `python -m subfeed.backup` at 03:30 server time. It uses SQLite's `.backup` to copy the live database to `<saved dir>/_db/subfeed-YYYYMMDD.db` and keeps the newest 14. It exits with an error and writes nothing if the saved-videos directory is not mounted.

```bash
systemctl enable --now subfeed-backup.timer
systemctl list-timers subfeed-backup.timer
systemctl start subfeed-backup.service && journalctl -u subfeed-backup -n 5
```

The Google refresh token is not in the database. Back up `/var/lib/subfeed/google-token.json` yourself if you care about not signing in again, or just sign in again.

If you have no saved-videos mount, the backup unit has nowhere to write. Either skip the timer and back up `/var/lib/subfeed/subfeed.db` another way (`sqlite3 /var/lib/subfeed/subfeed.db ".backup /somewhere/subfeed.db"`), or change `SUBFEED_SAVED_DIR`.

## 8. Google sign-in

Follow [google-setup.md](google-setup.md), put the client id and secret in the env file, and `systemctl restart subfeed`.

## 9. Build and install the TV app

Back in the [README](../README.md#setup), step "Build the APK".

## Updating the service

```bash
cd /path/to/your/clone && git pull
cp -a service/. /opt/subfeed/service/
chown -R subfeed:subfeed /opt/subfeed/service
su subfeed -c '/opt/subfeed/venv/bin/pip install -e /opt/subfeed/service'
systemctl restart subfeed
```

Take a copy of the database first if you want a way back: `sqlite3 /var/lib/subfeed/subfeed.db ".backup /var/lib/subfeed/subfeed.db.bak"`.

### Updating yt-dlp

YouTube changes its site often and yt-dlp releases fixes within days. `service/pyproject.toml` pins a version that was tested together with the service; when playback breaks (Settings shows "YouTube playback check failed"), try a newer one:

```bash
su subfeed -c '/opt/subfeed/venv/bin/pip install -U yt-dlp' && systemctl restart subfeed
```

Subfeed never upgrades yt-dlp by itself. The hourly canary (a known public video) only reports whether extraction works, and a bot-check error pauses background YouTube requests for 6 to 24 hours.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| TV app says it can't reach the server | Wrong `SUBFEED_API_BASE`, the CA in the APK is not the server's current root, or port 443 is blocked. Test with the `curl --cacert` line above. |
| 401 from every request | The token in the env file differs from the one the APK was built with. Rebuild the APK or fix the env file, then `systemctl restart subfeed`. |
| "Can't reach the NAS, so saving is paused." | `SUBFEED_SAVED_DIR` is not a mount point (or the mount is hung). `findmnt /srv/saved`. |
| Videos refuse to play, health shows a failing playback check | yt-dlp needs updating, or deno is missing from `PATH` (`yt-dlp -v` prints `JS runtimes:`). |
| Everything in "Other" | `OPENROUTER_API_KEY` is not set. Move channels by hand from the long-press menu. |
