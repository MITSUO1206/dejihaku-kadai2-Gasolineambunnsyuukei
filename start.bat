@echo off
rem 社内PC用の起動ファイル
rem  - デモモードを切って（実データ用）起動します
rem  - このPCからしか開けない設定（localhost）で起動します
cd /d "%~dp0"
set GASCARD_DEMO=0
start "" http://localhost:8501
".venv\Scripts\streamlit.exe" run app.py --server.address localhost --server.port 8501
