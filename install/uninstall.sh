#!/bin/bash

# Formatting stuff (guarded: safe to call in non-interactive/no-TTY shells,
# matching _common.sh's pattern)
bold=$(tput bold 2>/dev/null || true)
normal=$(tput sgr0 2>/dev/null || true)
red=$(tput setaf 1 2>/dev/null || true)

APPNAME="inkypi"
INSTALL_PATH="/usr/local/$APPNAME"
BINPATH="/usr/local/bin"
SERVICE_FILE="/etc/systemd/system/$APPNAME.service"
FAILURE_SERVICE_FILE="/etc/systemd/system/inkypi-failure.service"
DROPIN_DIR="/etc/systemd/system/inkypi.service.d"
STATE_DIR="/var/lib/inkypi"

echo_success() {
  echo -e "$1 [\e[32m\xE2\x9C\x94\e[0m]"
}

echo_override() {
  echo -e "\r$1"
}

echo_header() {
  echo -e "${bold}$1${normal}"
}

echo_error() {
  echo -e "${red}$1${normal} [\e[31m\xE2\x9C\x98\e[0m]\n"
}

check_permissions() {
  # Ensure the script is run with sudo
  if [ "$EUID" -ne 0 ]; then
    echo_error "ERROR: Uninstallation requires root privileges. Please run it with sudo."
    exit 1
  fi
}

stop_service() {
  echo "Stopping $APPNAME service"
  if /usr/bin/systemctl is-active --quiet "$APPNAME.service"
  then
    /usr/bin/systemctl stop "$APPNAME.service"
    echo_success "\tService stopped successfully."
  else
    echo_success "\tService is not running."
  fi
}

disable_service() {
  echo "Disabling $APPNAME service"
  if [ -f "$SERVICE_FILE" ]; then
    /usr/bin/systemctl disable "$APPNAME.service"
    rm -f "$SERVICE_FILE"
    echo_success "\tService disabled and removed."
  else
    echo_success "\tService file does not exist. Nothing to remove."
  fi

  # inkypi-failure.service is an OnFailure= sentinel unit, never enabled
  # itself (see install_failure_service_unit in _common.sh) — just remove
  # the unit file.
  if [ -f "$FAILURE_SERVICE_FILE" ]; then
    rm -f "$FAILURE_SERVICE_FILE"
    echo_success "\tFailure sentinel service removed."
  else
    echo_success "\tFailure sentinel service does not exist. Nothing to remove."
  fi

  # Memory-cap drop-in directory (install_memory_dropin in _common.sh).
  if [ -d "$DROPIN_DIR" ]; then
    rm -rf "$DROPIN_DIR"
    echo_success "\tMemory-cap drop-in removed."
  fi

  /usr/bin/systemctl daemon-reload
}

remove_files() {
  echo "Removing application files"

  # NOTE: $INSTALL_PATH/src is a symlink to the actual source checkout (see
  # install_src() in install.sh) — it is NOT a copy. `rm -rf "$INSTALL_PATH"`
  # below removes that symlink itself (it does not follow it into the real
  # checkout), so the git clone this was installed from — and device.json/
  # plugins.json inside it — is left untouched. Do not add per-file removal
  # of files under "$INSTALL_PATH/src" here; that would resolve through the
  # symlink and delete the user's real config.

  # Remove the installation directory
  if [ -d "$INSTALL_PATH" ]; then
    rm -rf "$INSTALL_PATH"
    echo_success "\tInstallation directory $INSTALL_PATH removed."
  else
    echo_success "\tInstallation directory $INSTALL_PATH does not exist."
  fi

  # Remove the executable
  if [ -f "$BINPATH/$APPNAME" ]; then
    rm -f "$BINPATH/$APPNAME"
    echo_success "\tExecutable $BINPATH/$APPNAME removed."
  else
    echo_success "\tExecutable $BINPATH/$APPNAME does not exist."
  fi

  # Runtime state (install-lock/failure breadcrumbs, prev_version, etc.)
  if [ -d "$STATE_DIR" ]; then
    rm -rf "$STATE_DIR"
    echo_success "\tRuntime state directory $STATE_DIR removed."
  fi
}

confirm_uninstall() {
  echo -e "${bold}Are you sure you want to uninstall $APPNAME? (y/N): ${normal}"
  echo "This removes the installed app, its systemd services, and its runtime state."
  echo "Your git checkout (including device.json/plugins.json inside it) is NOT touched."
  read -r confirmation
  if [[ "$confirmation" != "y" && "$confirmation" != "Y" ]]; then
    echo_error "Uninstallation cancelled."
    exit 1
  fi
}

check_permissions
confirm_uninstall
stop_service
disable_service
remove_files

echo_success "Uninstallation complete."
echo_header "All components of $APPNAME have been removed."
echo "Note: system-wide tuning made at install time (zram/earlyoom packages,"
echo "journald persistent-storage config, NetworkManager Wi-Fi powersave"
echo "override, and the SPI/I2C lines in config.txt) is intentionally left in"
echo "place, since it may be relied on by other software. Remove manually if"
echo "you no longer want it."
