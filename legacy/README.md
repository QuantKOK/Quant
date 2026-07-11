# Legacy (pre-pivot) code — archived, not part of MacroEdge

These files are artifacts from the original quant utility that predates the
MacroEdge pivot. They are **not** used by MacroEdge and are kept only for
reference/history.

- `strategy.py` — vwap / trend-following / stat-arb / option-proxy simulation
  helpers built on pandas + numpy.
- `M1` — a copy of `strategy.py` (previously wired into `run.ps1 -RunExample`).

MacroEdge itself is standard-library only and does not import pandas or numpy.
If you want to run this archived code, install its own dependencies:

```
pip install -r legacy/requirements.txt
```

The live, supported system lives in `macroedge/`. See the repository `README.md`
and run the end-to-end demo with `py -3 demo.py`.
