# M1 Test Layers

M1 uses three layers:

- `tests/unit` for deterministic logic (`UT-*`)
- `tests/component` for local component interactions (`CT-*`)
- `tests/e2e` for optional real-site validation (`E2E-*`)

Run entry:

```bash
python tools/run_m1_tests.py ut
python tools/run_m1_tests.py ct
python tools/run_m1_tests.py e2e
python tools/run_m1_tests.py all
```

List available targets:

```bash
python tools/run_m1_tests.py --list
```
