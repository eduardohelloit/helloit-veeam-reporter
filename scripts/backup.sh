#!/bin/bash
# =============================================================================
# backup.sh — Backup COMPLETO e CRIPTOGRAFADO do HelloIT Veeam Reporter
#   Gera UM arquivo  veeam-reporter_<data>.tar.gpg  (AES-256) contendo:
#     db.sql.gz    -> dump PostgreSQL
#     data.tar.gz  -> volumes uploads + branding
#     code.tar.gz  -> código da aplicação (INCLUI o .env)
#     RESTORE.txt  -> passo a passo de restauração
#   Ideal para subir em nuvem (Terabox etc.) sem expor os segredos.
#
# Senha: lida de $BACKUP_PASSFILE (default /etc/veeam-backup.pass, chmod 600)
#        ou da variável BACKUP_PASSPHRASE. Sem senha -> gera SEM criptografia
#        (com aviso). GUARDE A SENHA FORA DO SERVIDOR — sem ela o backup é
#        irrecuperável.
#
# Uso manual:  BACKUP_DEST=/mnt/drive ./scripts/backup.sh
# =============================================================================
set -euo pipefail

# ── CONFIG ───────────────────────────────────────────────────────────────────
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BACKUP_DEST="${BACKUP_DEST:-/var/backups/helloit-veeam-reporter}"
KEEP="${KEEP:-4}"
BACKUP_PASSFILE="${BACKUP_PASSFILE:-/etc/veeam-backup.pass}"
DB_SERVICE="db"; DB_USER="${POSTGRES_USER:-veeam}"; DB_NAME="${POSTGRES_DB:-veeam_reporter}"
WEB_SERVICE="web"
DC="sudo docker compose"

cd "$PROJECT_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DEST"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
echo "[backup $(date '+%F %T')] preparando ($STAMP)..."

# 1) Banco
echo "[backup] 1/3 dump do banco ($DB_NAME)..."
$DC exec -T "$DB_SERVICE" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$WORK/db.sql.gz"
# 2) Volumes
echo "[backup] 2/3 volumes (uploads + branding)..."
$DC exec -T "$WEB_SERVICE" tar czf - -C /data uploads branding > "$WORK/data.tar.gz"
# 3) Código
echo "[backup] 3/3 código..."
tar czf "$WORK/code.tar.gz" -C "$PROJECT_DIR" \
    --exclude='./uploads' --exclude='./reports' --exclude='__pycache__' .

cat > "$WORK/RESTORE.txt" <<EOF
Backup HelloIT Veeam Reporter — $STAMP
=========================================
db.sql.gz  -> dump PostgreSQL ($DB_NAME)
data.tar.gz-> volumes /data/uploads + /data/branding
code.tar.gz-> código da aplicação (INCLUI o .env com a senha do banco)

RESTAURAÇÃO (ordem importa — banco ANTES do app):
  1) Código:      mkdir app && tar xzf code.tar.gz -C app && cd app
  2) Só o banco:  sudo docker compose up -d db          # espere ~15s
  3) Restaure DB: gunzip -c ../db.sql.gz | sudo docker compose exec -T db \\
                    psql -U $DB_USER -d $DB_NAME
  4) Suba o app:  sudo docker compose up -d
  5) Volumes:     sudo docker compose exec -T web tar xzf - -C /data < ../data.tar.gz
  6) Reinicie:    sudo docker compose restart web
EOF

# ── senha ─────────────────────────────────────────────────────────────────────
PASS_MODE="none"
if [ -n "${BACKUP_PASSPHRASE:-}" ]; then PASS_MODE="env"
elif [ -f "$BACKUP_PASSFILE" ]; then PASS_MODE="file"; fi

if [ "$PASS_MODE" != "none" ]; then
    OUTFILE="$BACKUP_DEST/veeam-reporter_$STAMP.tar.gpg"
    GPG_PASS=(--pinentry-mode loopback)
    [ "$PASS_MODE" = "env" ] && GPG_PASS+=(--passphrase "$BACKUP_PASSPHRASE") \
                             || GPG_PASS+=(--passphrase-file "$BACKUP_PASSFILE")
    echo "[backup] cifrando (AES-256)..."
    tar cf - -C "$WORK" . | gpg --batch --yes "${GPG_PASS[@]}" \
        --cipher-algo AES256 --compress-algo none -o "$OUTFILE" --symmetric
    # LEIAME em claro ao lado (só instrução de decifrar, sem segredo)
    cat > "$BACKUP_DEST/veeam-reporter_$STAMP.LEIAME.txt" <<EOF
Backup CRIPTOGRAFADO (AES-256/GPG). Para abrir:
  gpg --output backup.tar --decrypt veeam-reporter_$STAMP.tar.gpg   # pede a senha
  tar xf backup.tar                                                 # extrai os arquivos
  cat RESTORE.txt                                                   # instruções
Sem a senha (guardada fora do servidor) NÃO há como recuperar.
EOF
    SIZE="$(du -sh "$OUTFILE" | cut -f1)"
    echo "[backup] cifrado: $OUTFILE  ($SIZE)"
    PATTERN_GPG=1
else
    OUTDIR="$BACKUP_DEST/veeam-reporter_$STAMP"
    mkdir -p "$OUTDIR"; cp "$WORK"/* "$OUTDIR"/
    SIZE="$(du -sh "$OUTDIR" | cut -f1)"
    echo "[backup] AVISO: SEM criptografia (defina $BACKUP_PASSFILE). $OUTDIR ($SIZE)"
    PATTERN_GPG=0
fi

# ── rotação (mantém as últimas $KEEP) ────────────────────────────────────────
if [ "$PATTERN_GPG" = "1" ]; then
    ls -1t "$BACKUP_DEST"/veeam-reporter_*.tar.gpg 2>/dev/null | tail -n +"$((KEEP + 1))" \
        | while read -r f; do rm -f "$f" "${f%.tar.gpg}.LEIAME.txt"; done
else
    ls -1dt "$BACKUP_DEST"/veeam-reporter_*/ 2>/dev/null | tail -n +"$((KEEP + 1))" \
        | xargs -r rm -rf
fi
echo "[backup $(date '+%F %T')] OK (mantendo $KEEP cópias)"
