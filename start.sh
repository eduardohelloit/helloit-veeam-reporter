#!/bin/sh
set -e

CERT_DIR="/data/certs"
mkdir -p "$CERT_DIR"

# ── Generate self-signed TLS certificate (valid 10 years) ─────────────────────
if [ ! -f "$CERT_DIR/cert.pem" ] || [ ! -f "$CERT_DIR/key.pem" ]; then
  echo "[SSL] Gerando certificado SSL self-signed..."

  # Create OpenSSL config with SAN for localhost + any LAN IP
  cat > /tmp/ssl.cnf <<EOF
[req]
distinguished_name = req_dn
x509_extensions    = v3_req
prompt             = no

[req_dn]
C  = BR
ST = Sao Paulo
L  = Sao Paulo
O  = HelloIT
CN = veeam-reporter

[v3_req]
subjectAltName = @alt_names
keyUsage       = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[alt_names]
DNS.1 = localhost
DNS.2 = veeam-reporter
IP.1  = 127.0.0.1
EOF

  openssl req -x509 -nodes -days 3650 \
    -newkey rsa:2048 \
    -keyout "$CERT_DIR/key.pem" \
    -out    "$CERT_DIR/cert.pem" \
    -config /tmp/ssl.cnf \
    2>/dev/null

  rm -f /tmp/ssl.cnf
  echo "[SSL] Certificado gerado em $CERT_DIR"
else
  echo "[SSL] Certificado existente encontrado."
fi

# ── Generate stable session secret (persists across restarts) ─────────────────
SECRET_FILE="$CERT_DIR/session.key"
if [ -z "$SESSION_SECRET" ]; then
  if [ ! -f "$SECRET_FILE" ]; then
    openssl rand -hex 32 > "$SECRET_FILE"
    echo "[AUTH] Chave de sessão gerada."
  fi
  export SESSION_SECRET=$(cat "$SECRET_FILE")
fi

echo "[APP] Iniciando HelloIT Veeam Reporter v1.0.0 em HTTPS :8443"

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8443 \
  --ssl-keyfile  "$CERT_DIR/key.pem" \
  --ssl-certfile "$CERT_DIR/cert.pem" \
  --proxy-headers \
  --forwarded-allow-ips "*"
