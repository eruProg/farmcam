@echo off
rem Mark the 120 machine photos as healthy / fungus / unsure (keys H F U). Keep this window open.
cd /d "%~dp0rpi_server"
py -3.11 label_health.py
pause
