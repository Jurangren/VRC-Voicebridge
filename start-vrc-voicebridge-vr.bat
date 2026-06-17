@echo off
chcp 65001 >nul
start "VRC-Bridge" python tools\vr_controller_bridge.py
python main.py --vr
taskkill /f /fi "WINDOWTITLE eq VRC-Bridge" >nul 2>&1