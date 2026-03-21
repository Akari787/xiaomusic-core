# Relay Test Layers

> 本文属于开发测试说明，不作为当前正式接口说明。

Relay 子系统使用三层测试：

- `tests/unit`：确定性逻辑验证（`UT-*`）
- `tests/component`：本地组件交互验证（`CT-*`）
- `tests/e2e`：可选的真实站点验证（`E2E-*`）

统一入口：

```bash
python tools/run_relay_tests.py ut
python tools/run_relay_tests.py ct
python tools/run_relay_tests.py e2e
python tools/run_relay_tests.py all
```

查看可用目标：

```bash
python tools/run_relay_tests.py --list
```
