#!/usr/bin/env bash
set -euo pipefail

APP_NAME="service-check"
SRC_DIR="/opt/service-check-src"
VENV_DIR="/opt/service-check-venv"
CONFIG_DIR="/etc/service-check"
DROPIN_DIR="${CONFIG_DIR}/service-check.ini.d"
STATE_DIR="/var/lib/service-check"
SYSTEMD_DIR="/etc/systemd/system"

log() {
  printf '[%s] %s\n' "${APP_NAME}" "$*"
}

die() {
  printf '[%s] ERROR: %s\n' "${APP_NAME}" "$*" >&2
  exit 1
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "run this installer as root, for example: sudo bash install.sh"
  fi
}

require_linux() {
  [[ "$(uname -s)" == "Linux" ]] || die "this installer targets Linux hosts with systemd"
}

install_packages() {
  if command -v apt-get >/dev/null 2>&1; then
    log "Installing OS prerequisites with apt"
    apt-get update
    apt-get install -y git python3 python3-venv rsync
    return
  fi

  if command -v dnf >/dev/null 2>&1; then
    log "Installing OS prerequisites with dnf"
    dnf install -y git python3 rsync
    return
  fi

  if command -v yum >/dev/null 2>&1; then
    log "Installing OS prerequisites with yum"
    yum install -y git python3 rsync
    return
  fi

  log "No supported package manager found; assuming git, python3, venv support, and rsync are installed"
}

sync_source_checkout() {
  local current_dir
  current_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  if [[ "${current_dir}" == "${SRC_DIR}" ]]; then
    log "Using source checkout at ${SRC_DIR}"
    return
  fi

  log "Syncing current checkout to ${SRC_DIR}"
  install -d "${SRC_DIR}"
  rsync -a --delete \
    --exclude '.idea/' \
    --exclude '.service-check/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    "${current_dir}/" "${SRC_DIR}/"
}

install_python_package() {
  log "Creating virtual environment at ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"

  log "Installing package into virtual environment"
  "${VENV_DIR}/bin/python" -m pip install --upgrade "${SRC_DIR}"
}

install_runtime_files() {
  log "Creating runtime directories"
  install -d "${CONFIG_DIR}" "${DROPIN_DIR}" "${STATE_DIR}"

  log "Installing default config without overwriting existing files"
  cp -n "${SRC_DIR}/examples/service-check.ini" "${CONFIG_DIR}/service-check.ini"
  cp -n "${SRC_DIR}/examples/service-check.ini.d/10-version.ini" "${DROPIN_DIR}/10-version.ini"

  log "Installing systemd units"
  install -m 0644 "${SRC_DIR}/systemd/service-check.service" "${SYSTEMD_DIR}/service-check.service"
  install -m 0644 "${SRC_DIR}/systemd/service-check.timer" "${SYSTEMD_DIR}/service-check.timer"
}

enable_systemd_timer() {
  command -v systemctl >/dev/null 2>&1 || die "systemctl not found; install systemd units manually"

  log "Reloading systemd and enabling timer"
  systemctl daemon-reload
  systemctl enable --now service-check.timer
}

verify_installation() {
  log "Verifying installed command"
  "${VENV_DIR}/bin/service-check" --version

  log "Verifying configuration with a dry run"
  "${VENV_DIR}/bin/service-check" --config "${CONFIG_DIR}/service-check.ini" --all --dry-run

  log "Checking timer status"
  systemctl is-enabled service-check.timer >/dev/null
  systemctl is-active service-check.timer >/dev/null
}

main() {
  require_root
  require_linux
  install_packages
  sync_source_checkout
  install_python_package
  install_runtime_files
  enable_systemd_timer
  verify_installation
  log "Installation complete"
}

main "$@"
