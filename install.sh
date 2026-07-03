#!/usr/bin/env bash
set -euo pipefail

APP_NAME="service-check"
SRC_DIR="/opt/service-check-src"
VENV_DIR="/opt/service-check-venv"
CONFIG_DIR="/etc/service-check"
DROPIN_DIR="${CONFIG_DIR}/service-check.ini.d"
STATE_DIR="/var/lib/service-check"
SYSTEMD_DIR="/etc/systemd/system"
BIN_LINK="/usr/local/bin/service-check"

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
  if [[ ! -f "${CONFIG_DIR}/service-check.ini" ]]; then
    install -m 0644 "${SRC_DIR}/examples/service-check.ini" "${CONFIG_DIR}/service-check.ini"
  else
    repair_known_bad_config_defaults "${CONFIG_DIR}/service-check.ini"
  fi
  cp -n "${SRC_DIR}/examples/service-check.ini.d/10-version.ini" "${DROPIN_DIR}/10-version.ini"
  install_check_examples
  disable_known_old_tcp_dropin

  log "Installing systemd units"
  install -m 0644 "${SRC_DIR}/systemd/service-check.service" "${SYSTEMD_DIR}/service-check.service"
  install -m 0644 "${SRC_DIR}/systemd/service-check.timer" "${SYSTEMD_DIR}/service-check.timer"

  log "Installing ${BIN_LINK}"
  ln -sfn "${VENV_DIR}/bin/service-check" "${BIN_LINK}"
}

install_check_examples() {
  local example_file
  local target_file
  local copied=0

  shopt -s nullglob
  for example_file in "${SRC_DIR}"/service_check/checks/*/*.example.ini; do
    target_file="${DROPIN_DIR}/$(basename "${example_file}" .example.ini).ini.skip"
    if [[ -e "${target_file}" ]]; then
      log "Skipping existing check example ${target_file}"
      continue
    fi
    install -m 0644 "${example_file}" "${target_file}"
    copied=$((copied + 1))
  done
  shopt -u nullglob

  if [[ "${copied}" -gt 0 ]]; then
    log "Installed ${copied} inactive check example(s) as ${DROPIN_DIR}/*.ini.skip"
    log "Copy an .ini.skip file to .ini and edit it to enable that check"
  fi
}

disable_known_old_tcp_dropin() {
  local old_dropin="${DROPIN_DIR}/10-tcp.ini"
  local disabled_dropin="${DROPIN_DIR}/10-tcp.ini.disabled"

  if [[ ! -f "${old_dropin}" ]]; then
    return
  fi

  if grep -q '^\[example_tcp_open\]$' "${old_dropin}" \
    && grep -q '^check=tcp_port$' "${old_dropin}" \
    && grep -q '^host=127\.0\.0\.1$' "${old_dropin}" \
    && grep -q '^port=80$' "${old_dropin}"; then
    log "Disabling obsolete default TCP example ${old_dropin}"
    mv -n "${old_dropin}" "${disabled_dropin}"
  fi
}

repair_known_bad_config_defaults() {
  local config_file="$1"

  if grep -q '^state_file=\./\.service-check/state\.json$' "${config_file}"; then
    log "Repairing relative state_file in ${config_file}"
    sed -i 's#^state_file=\./\.service-check/state\.json$#state_file=/var/lib/service-check/state.json#' "${config_file}"
  fi

  if grep -q '^lock_file=\./\.service-check/state\.lock$' "${config_file}"; then
    log "Repairing relative lock_file in ${config_file}"
    sed -i 's#^lock_file=\./\.service-check/state\.lock$#lock_file=/var/lib/service-check/state.lock#' "${config_file}"
  fi
}

enable_systemd_timer() {
  command -v systemctl >/dev/null 2>&1 || die "systemctl not found; install systemd units manually"

  log "Reloading systemd and enabling timer"
  systemctl daemon-reload
  systemctl enable --now service-check.timer
}

verify_installation() {
  log "Verifying installed command"
  service-check --version

  log "Verifying configuration with a dry run"
  service-check --config "${CONFIG_DIR}/service-check.ini" --all --dry-run

  log "Running checks once without local notifications"
  set +e
  service-check --config "${CONFIG_DIR}/service-check.ini" --all --no-notify
  local check_status=$?
  set -e
  if [[ "${check_status}" -ne 0 ]]; then
    log "Initial check run exited ${check_status}; inspect configured checks and journal output"
  fi
  test -f "${STATE_DIR}/state.json"

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
