Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Не удалось создать venv" }
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости" }
}
.venv\Scripts\python.exe main.py
