@echo off
REM Starts the llmfin MCP server over HTTP, for MCP clients that connect by URL
REM rather than by spawning a stdio process.
REM Leave this window open while using the tools (minimize it).
REM Connector URL to add in your client:  http://127.0.0.1:8747/mcp

REM Run from this script's own directory, so the repo can live anywhere.
cd /d "%~dp0"

.venv\Scripts\python.exe -m llmfin.server --http --port 8747
pause
