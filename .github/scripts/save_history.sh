#!/usr/bin/env bash
# Save data/radar.db, gzip-compressed, as the only file of the radar-data branch.
# Safe while the radar is running: it copies the database with SQLite's backup API.
set -euo pipefail

[ -f data/radar.db ] || exit 0
tmp=$(mktemp -d)

python - "$tmp/radar.db.gz" <<'PY'
import gzip, os, shutil, sqlite3, sys

target = sys.argv[1]
snapshot = target[:-3]
src = sqlite3.connect("data/radar.db", timeout=60)
dst = sqlite3.connect(snapshot)
src.backup(dst)
dst.close()
src.close()
with open(snapshot, "rb") as raw, gzip.open(target, "wb", compresslevel=9) as packed:
    shutil.copyfileobj(raw, packed)
os.remove(snapshot)
size_mb = os.path.getsize(target) / 1e6
print(f"radar.db.gz: {size_mb:.1f} MB")
if size_mb > 95:
    sys.exit("El historico comprimido supera 95 MB: GitHub no admite archivos de mas de 100 MB")
PY

cd "$tmp"
git init -q -b radar-data
git config user.name "radar-bot"
git config user.email "radar-bot@users.noreply.github.com"
git add radar.db.gz
git commit -q -m "data: $(date -u +%Y-%m-%dT%H:%MZ)"
git push -q --force "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" radar-data
echo "Historico guardado"
