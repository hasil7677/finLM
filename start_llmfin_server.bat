@echo off
REM Starts the llmfin MCP server over HTTP for Claude Desktop connectors.
REM Leave this window open while using the tools (minimize it).
REM Connector URL to add in Claude Desktop:  http://127.0.0.1:8747/mcp
cd /d C:\Users\Sahil\Downloads\finLM\finLLM
.venv\Scripts\python.exe -m llmfin.server --http --port 8747
pause
