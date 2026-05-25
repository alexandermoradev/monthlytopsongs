#!/usr/bin/env bash
#
# run_monthly.sh — Lanzador del wrap mensual para cron / systemd (Linux).
#
# Hace cd a la carpeta del proyecto (sea cual sea el cwd de cron) y ejecuta el
# management command con el Python del entorno virtual. Cualquier argumento
# extra se pasa tal cual (p. ej. ./run_monthly.sh --public).
#
set -euo pipefail

# Carpeta donde vive este script = raíz del proyecto.
cd "$(dirname "$(readlink -f "$0")")"

exec ./.venv/bin/python manage.py generate_monthly_playlist "$@"
