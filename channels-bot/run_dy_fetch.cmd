@echo off
rem ASCII-only: fetch douyin video urls (runs outside sandbox via scheduled task)
cd /d "%~dp0"
"..\nodejs\node-v20.18.2-win-x64\node.exe" dy_fetch_urls.js > dy_fetch_run.log 2>&1
