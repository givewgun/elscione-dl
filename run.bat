@echo off
:: Launch the elscione-dl interactive TUI
:: Double-click this to open the catalog browser.

chcp 65001 >nul
cd /d "%~dp0"
python -m elscione_dl
pause
