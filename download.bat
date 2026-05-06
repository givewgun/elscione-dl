@echo off
:: Quick download helper — edit the TITLE and FORMATS lines below, then double-click.
::
:: TITLE: paste the path from the server URL, e.g.
::   /Officially Translated Light Novels/Overlord/
::
:: FORMATS: epub   or   pdf   or   epub,pdf

set TITLE=/Officially Translated Light Novels/I'm in Love With the Villainess - She's So Cheeky for a Commoner/
set FORMATS=epub

chcp 65001 >nul
cd /d "%~dp0"
python -m elscione_dl download "%TITLE%" --formats %FORMATS%
echo.
pause
