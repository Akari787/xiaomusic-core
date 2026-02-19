# Network Audio Test Layers

网络音频链路使用三层测试：

- `tests/unit` for deterministic logic (`UT-*`)
- `tests/component` for local component interactions (`CT-*`)
- `tests/e2e` for optional real-site validation (`E2E-*`)

Run entry:

```bash
python tools/run_network_audio_tests.py ut
python tools/run_network_audio_tests.py ct
python tools/run_network_audio_tests.py e2e
python tools/run_network_audio_tests.py all
```

List available targets:

```bash
python tools/run_network_audio_tests.py --list
```

Use the unified runner above as the only supported entry.
