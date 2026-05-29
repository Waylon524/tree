@echo off
REM Start local Qwen3-Embedding-4B-Q8_0 embedding server (Windows)
REM
REM Usage:
REM   tree_engine\scripts\start-embed-server.bat                    all GPU layers (default)
REM   tree_engine\scripts\start-embed-server.bat --n-gpu-layers 0   CPU only
REM   tree_engine\scripts\start-embed-server.bat --n-gpu-layers -1  force all GPU
REM   tree_engine\scripts\start-embed-server.bat --n-ctx 32768      context length
REM   tree_engine\scripts\start-embed-server.bat --n-seq-max 1      parallel embedding sequences

setlocal

set PROJECT_ROOT=%~dp0..\..
cd /d "%PROJECT_ROOT%"
set PYTHONPATH=%PROJECT_ROOT%\tree_engine;%PYTHONPATH%
set PYTHON_BIN=python
if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" set PYTHON_BIN=%PROJECT_ROOT%\.venv\Scripts\python.exe

REM Load config if present
if exist "%USERPROFILE%\.tree\config.env" (
    for /f "usebackq tokens=1,* delims==" %%a in ("%USERPROFILE%\.tree\config.env") do (
        if not "%%a"=="" if not "%%a:~0,1%"=="#" (
            set "%%a=%%b"
        )
    )
)
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        if not "%%a"=="" if not "%%a:~0,1%"=="#" (
            set "%%a=%%b"
        )
    )
)
if exist .tree\config.env (
    for /f "usebackq tokens=1,* delims==" %%a in (".tree\config.env") do (
        if not "%%a"=="" if not "%%a:~0,1%"=="#" (
            set "%%a=%%b"
        )
    )
)

if "%EMBED_PORT%"=="" set EMBED_PORT=8788
if "%EMBED_N_GPU_LAYERS%"=="" set EMBED_N_GPU_LAYERS=-1
if "%EMBED_N_CTX%"=="" set EMBED_N_CTX=32768
if "%EMBED_N_SEQ_MAX%"=="" set EMBED_N_SEQ_MAX=1

REM Export proxy for HuggingFace downloads (if set in config)
if not "%HTTP_PROXY%"=="" set HTTPS_PROXY=%HTTP_PROXY%

echo Starting Qwen3-Embedding-4B-Q8_0 embedding server on port %EMBED_PORT% (n_gpu_layers=%EMBED_N_GPU_LAYERS%, n_ctx=%EMBED_N_CTX%, n_seq_max=%EMBED_N_SEQ_MAX%)
echo Model: Qwen/Qwen3-Embedding-4B-GGUF / Qwen3-Embedding-4B-Q8_0.gguf
echo API endpoint: http://localhost:%EMBED_PORT%/v1/embeddings
echo.

"%PYTHON_BIN%" -m rag.server --port %EMBED_PORT% --n-gpu-layers %EMBED_N_GPU_LAYERS% --n-ctx %EMBED_N_CTX% --n-seq-max %EMBED_N_SEQ_MAX% %*
