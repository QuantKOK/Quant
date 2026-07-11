# run.ps1 - helper script to setup environment, install deps and run tests
param(
    [switch]$CreateVenv,
    [switch]$Install,
    [switch]$Test,
    [switch]$RunExample
)

if ($CreateVenv) {
    py -3 -m venv .venv
    Write-Host "Created .venv"
}

if ($Install) {
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    Write-Host "Dependencies installed"
}

if ($Test) {
    .\.venv\Scripts\python.exe -m pytest -q
}

if ($RunExample) {
    # End-to-end offline MacroEdge demo (contract -> candidate -> settlement ->
    # performance -> dashboard). Writes only to a throwaway temp directory.
    $env:PYTHONIOENCODING = "utf-8"
    if (Test-Path .\.venv\Scripts\python.exe) {
        .\.venv\Scripts\python.exe demo.py
    } else {
        py -3 demo.py
    }
}

if (-not ($CreateVenv -or $Install -or $Test -or $RunExample)) {
    Write-Host "Usage: .\run.ps1 -CreateVenv -Install -Test -RunExample"
}
