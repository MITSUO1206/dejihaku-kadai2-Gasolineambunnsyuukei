@echo off
rem 社内PC用の起動ファイル：このPCからしか開けない設定（localhost）で起動します
cd /d "%~dp0"
start "" http://localhost:8501
".venv\Scripts\streamlit.exe" run app.py --server.address localhost --server.port 8501
