@echo off
chcp 65001 >nul
cd /d %~dp0
start "VRC-Bridge" python tools\vr_controller_bridge.py
python main.py --vr