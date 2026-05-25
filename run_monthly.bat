@echo off
REM run_monthly.bat - Lanzador del wrap mensual para Task Scheduler (Windows).
REM Hace cd a la carpeta del proyecto y ejecuta el command con el Python del venv.
REM Los argumentos extra se pasan tal cual (p. ej. run_monthly.bat --public).

cd /d "%~dp0"
".venv\Scripts\python.exe" manage.py generate_monthly_playlist %*
