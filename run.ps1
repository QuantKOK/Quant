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
    .\.venv\Scripts\python.exe M1
}

if (-not ($CreateVenv -or $Install -or $Test -or $RunExample)) {
    Write-Host "Usage: .\run.ps1 -CreateVenv -Install -Test -RunExample"
}
