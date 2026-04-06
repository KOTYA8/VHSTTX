@echo off
setlocal
cd /d "%~dp0\..\.."
powershell -ExecutionPolicy Bypass -File "misc\windows\build-vhsttx.ps1"
