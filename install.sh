#!/usr/bin/env bash
set -Eeuo pipefail

ASSUME_YES=false
APP_NAME="${APP_NAME:-veeam-weekly-reporter}"
APP_PORT="${APP_PORT:-443}"
POSTGRES_DB="${POSTGRES_DB:-veeam_reporter}"
POSTGRES_USER="${POSTGRES_USER:-veeam}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.local}"
INSTALL_DOCKER="${INSTALL_DOCKER:-true}"
RESET_DATA="${RESET_DATA:-false}"

log() {
  printf '\n[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

fail() {
  printf '\nERRO: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

usage() {
  cat <<'EOF'
Uso:
  ./install.sh              Modo assistido
  ./install.sh --yes        Modo automatico com defaults/variaveis de ambiente

Variaveis aceitas:
  APP_NAME                  Nome da stack Docker
  APP_PORT                  Porta HTTPS externa
  POSTGRES_DB               Nome do banco
  POSTGRES_USER             Usuario do banco
  POSTGRES_PASSWORD         Senha do banco
  ADMIN_USERNAME            Usuario admin inicial
  ADMIN_PASSWORD            Senha admin inicial
  ADMIN_EMAIL               E-mail do admin inicial
  INSTALL_DOCKER            true/false
  RESET_DATA                true/false

Exemplo:
  APP_PORT=444 ADMIN_PASSWORD='<senha-forte>' ./install.sh --yes
EOF
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -y|--yes|--non-interactive)
        ASSUME_YES=true
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "Argumento desconhecido: $1"
        ;;
    esac
    shift
  done
}

is_interactive() {
  [ "$ASSUME_YES" != "true" ] && [ -t 0 ]
}

prompt_value() {
  local var_name="$1"
  local label="$2"
  local default_value="$3"
  local value

  if ! is_interactive; then
    printf -v "$var_name" '%s' "$default_value"
    return
  fi

  read -r -p "${label} [${default_value}]: " value
  value="${value:-$default_value}"
  printf -v "$var_name" '%s' "$value"
}

prompt_secret_or_generate() {
  local var_name="$1"
  local label="$2"
  local current_value="$3"
  local value confirm

  if [ -n "$current_value" ]; then
    printf -v "$var_name" '%s' "$current_value"
    return
  fi

  if ! is_interactive; then
    printf -v "$var_name" '%s' "$(generate_secret)"
    return
  fi

  while true; do
    read -r -s -p "${label} [Enter para gerar automaticamente]: " value
    printf '\n'
    if [ -z "$value" ]; then
      printf -v "$var_name" '%s' "$(generate_secret)"
      return
    fi

    read -r -s -p "Confirme ${label}: " confirm
    printf '\n'
    if [ "$value" = "$confirm" ]; then
      printf -v "$var_name" '%s' "$value"
      return
    fi
    printf 'As senhas nao conferem. Tente novamente.\n'
  done
}

prompt_bool() {
  local var_name="$1"
  local label="$2"
  local default_value="$3"
  local suffix answer

  if ! is_interactive; then
    printf -v "$var_name" '%s' "$default_value"
    return
  fi

  if [ "$default_value" = "true" ]; then
    suffix="S/n"
  else
    suffix="s/N"
  fi

  while true; do
    read -r -p "${label} [${suffix}]: " answer
    answer="${answer:-$default_value}"
    case "${answer,,}" in
      s|sim|y|yes|true)
        printf -v "$var_name" '%s' "true"
        return
        ;;
      n|nao|não|no|false)
        printf -v "$var_name" '%s' "false"
        return
        ;;
      *)
        printf 'Responda com sim ou nao.\n'
        ;;
    esac
  done
}

validate_port() {
  case "$APP_PORT" in
    ''|*[!0-9]*)
      fail "Porta invalida: ${APP_PORT}"
      ;;
  esac
  if [ "$APP_PORT" -lt 1 ] || [ "$APP_PORT" -gt 65535 ]; then
    fail "Porta fora do intervalo permitido: ${APP_PORT}"
  fi
}

show_intro() {
  if ! is_interactive; then
    return
  fi

  cat <<'EOF'

Instalador do HelloIT Veeam Reporter

Este assistente vai preparar o Docker, criar o .env, subir PostgreSQL zerado
e publicar a aplicacao em HTTPS.

Pressione Enter para aceitar o valor sugerido entre colchetes.
EOF
}

collect_settings() {
  show_intro
  prompt_value APP_NAME "Nome da stack Docker" "$APP_NAME"
  prompt_value APP_PORT "Porta HTTPS externa" "$APP_PORT"
  prompt_value POSTGRES_DB "Nome do banco PostgreSQL" "$POSTGRES_DB"
  prompt_value POSTGRES_USER "Usuario do banco PostgreSQL" "$POSTGRES_USER"
  prompt_secret_or_generate POSTGRES_PASSWORD "Senha do banco PostgreSQL" "$POSTGRES_PASSWORD"
  prompt_value ADMIN_USERNAME "Usuario admin inicial" "$ADMIN_USERNAME"
  prompt_secret_or_generate ADMIN_PASSWORD "Senha do admin inicial" "$ADMIN_PASSWORD"
  prompt_value ADMIN_EMAIL "E-mail do admin inicial" "$ADMIN_EMAIL"
  prompt_bool INSTALL_DOCKER "Instalar Docker/Compose se estiver ausente?" "$INSTALL_DOCKER"
  prompt_bool RESET_DATA "Zerar dados existentes desta stack antes de subir?" "$RESET_DATA"
  validate_port
}

confirm_settings() {
  local masked_db masked_admin answer
  masked_db="$(printf '%*s' "${#POSTGRES_PASSWORD}" '' | tr ' ' '*')"
  masked_admin="$(printf '%*s' "${#ADMIN_PASSWORD}" '' | tr ' ' '*')"

  cat <<EOF

Resumo da instalacao:
  Stack Docker:       ${APP_NAME}
  Porta HTTPS:        ${APP_PORT}
  Banco:              ${POSTGRES_DB}
  Usuario banco:      ${POSTGRES_USER}
  Senha banco:        ${masked_db}
  Usuario admin:      ${ADMIN_USERNAME}
  Senha admin:        ${masked_admin}
  E-mail admin:       ${ADMIN_EMAIL}
  Instalar Docker:    ${INSTALL_DOCKER}
  Zerar dados:        ${RESET_DATA}
EOF

  if ! is_interactive; then
    return
  fi

  read -r -p "Continuar com a instalacao? [S/n]: " answer
  answer="${answer:-s}"
  case "${answer,,}" in
    s|sim|y|yes) ;;
    *) fail "Instalacao cancelada pelo usuario." ;;
  esac
}

require_ubuntu() {
  if [ ! -r /etc/os-release ]; then
    fail "Nao consegui identificar o sistema operacional."
  fi

  . /etc/os-release
  if [ "${ID:-}" != "ubuntu" ]; then
    fail "Este instalador foi validado para Ubuntu 24.04. Sistema atual: ${PRETTY_NAME:-desconhecido}."
  fi

  case "${VERSION_ID:-}" in
    24.04|24.04.*) ;;
    *) log "Aviso: sistema atual e ${PRETTY_NAME:-Ubuntu}, mas o alvo recomendado e Ubuntu 24.04." ;;
  esac
}

require_project_files() {
  local missing=0
  for path in Dockerfile docker-compose.yml requirements.txt start.sh app alembic alembic.ini; do
    if [ ! -e "$path" ]; then
      printf 'Arquivo/diretorio ausente: %s\n' "$path" >&2
      missing=1
    fi
  done
  [ "$missing" -eq 0 ] || fail "Execute este script na raiz do projeto."
}

ensure_sudo() {
  if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
  else
    SUDO="sudo"
    need_cmd sudo || fail "sudo nao esta instalado."
    $SUDO -v
  fi
}

install_docker_if_needed() {
  if need_cmd docker && docker compose version >/dev/null 2>&1; then
    log "Docker e Docker Compose ja estao instalados."
    return
  fi

  [ "$INSTALL_DOCKER" = "true" ] || fail "Docker/Compose ausentes. Instale manualmente ou permita INSTALL_DOCKER=true."

  log "Instalando Docker Engine e plugin Compose..."
  $SUDO apt-get update
  $SUDO apt-get install -y ca-certificates curl gnupg lsb-release
  $SUDO install -m 0755 -d /etc/apt/keyrings

  if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
  fi

  . /etc/os-release
  local codename="${VERSION_CODENAME:-noble}"
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu %s stable\n' \
    "$(dpkg --print-architecture)" "$codename" | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null

  $SUDO apt-get update
  $SUDO apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  $SUDO systemctl enable --now docker
}

generate_secret() {
  if need_cmd openssl; then
    openssl rand -hex 24
  else
    tr -dc 'A-Za-z0-9' </dev/urandom | head -c 48
  fi
}

write_env_file() {
  if [ -f .env ]; then
    log "Arquivo .env existente preservado."
    return
  fi

  umask 077
  cat > .env <<EOF
POSTGRES_DB=${POSTGRES_DB}
POSTGRES_USER=${POSTGRES_USER}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}

ADMIN_USERNAME=${ADMIN_USERNAME}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
ADMIN_EMAIL=${ADMIN_EMAIL}
EOF

  log "Arquivo .env criado. Guarde a senha inicial abaixo:"
  printf '  Usuario admin: %s\n' "$ADMIN_USERNAME"
  printf '  Senha admin:   %s\n' "$ADMIN_PASSWORD"
}

patch_compose_port_if_needed() {
  if grep -q "\"${APP_PORT}:8443\"" docker-compose.yml; then
    return
  fi

  log "Ajustando porta externa do docker-compose.yml para ${APP_PORT}:8443."
  sed -i -E "s/\"[0-9]+:8443\"/\"${APP_PORT}:8443\"/" docker-compose.yml
}

reset_data_if_requested() {
  if [ "$RESET_DATA" != "true" ]; then
    return
  fi

  log "RESET_DATA=true informado. Removendo containers e volumes desta stack."
  $SUDO docker compose -p "$APP_NAME" down -v --remove-orphans
}

deploy_stack() {
  log "Construindo e subindo a stack ${APP_NAME}..."
  $SUDO docker compose -p "$APP_NAME" up -d --build
}

wait_for_web() {
  local url="https://127.0.0.1:${APP_PORT}/login"
  log "Validando aplicacao em ${url}..."

  for _ in $(seq 1 60); do
    if curl -kfsS -o /dev/null "$url"; then
      log "Aplicacao respondeu com sucesso."
      return
    fi
    sleep 2
  done

  $SUDO docker compose -p "$APP_NAME" ps || true
  $SUDO docker compose -p "$APP_NAME" logs --tail=120 web || true
  fail "A aplicacao nao respondeu em ${url} dentro do tempo esperado."
}

print_summary() {
  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  [ -n "$ip" ] || ip="<IP-DO-SERVIDOR>"

  log "Deploy concluido."
  printf '\nAcesse:\n'
  if [ "$APP_PORT" = "443" ]; then
    printf '  https://%s\n' "$ip"
  else
    printf '  https://%s:%s\n' "$ip" "$APP_PORT"
  fi
  printf '\nComandos uteis:\n'
  printf '  sudo docker compose -p %s ps\n' "$APP_NAME"
  printf '  sudo docker compose -p %s logs -f web\n' "$APP_NAME"
  printf '  sudo docker compose -p %s down\n' "$APP_NAME"
}

main() {
  parse_args "$@"
  require_ubuntu
  require_project_files
  collect_settings
  confirm_settings
  ensure_sudo
  install_docker_if_needed
  write_env_file
  patch_compose_port_if_needed
  reset_data_if_requested
  deploy_stack
  wait_for_web
  print_summary
}

main "$@"
