# Quant

Small strategy utilities and tests.

Files:
- `strategy.py` - core functions: `vwap`, `make_trend_following`, `make_stat_arb`, `simulate_option_proxy`.
- `M1` - example runner (run `python M1` to see example output).
- `tests/` - pytest tests.

Quick start (Windows PowerShell):
```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest -q
```

CI: GitHub Actions workflow is configured in `.github/workflows/pytest.yml` to run tests on push/PR to `main`.
