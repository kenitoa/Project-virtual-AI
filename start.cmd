@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m uv run --locked python -m virtual_ai.rehearsal
pause
