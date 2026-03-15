# Network Audio Test Layers

> 术语说明：本文件中的 `Network Audio` 主要指历史测试工具与目录命名；来源语义请使用 `Site Media` / `Direct URL`。
> 本文属于开发测试说明，不作为当前正式接口说明。

历史网络音频链路使用三层测试：

- `tests/unit`：确定性逻辑验证（`UT-*`）
- `tests/component`：本地组件交互验证（`CT-*`）
- `tests/e2e`：可选的真实站点验证（`E2E-*`）

统一入口：

```bash
python tools/run_network_audio_tests.py ut
python tools/run_network_audio_tests.py ct
python tools/run_network_audio_tests.py e2e
python tools/run_network_audio_tests.py all
```

查看可用目标：

```bash
python tools/run_network_audio_tests.py --list
```

以上 runner 是该历史测试集合的统一入口。
