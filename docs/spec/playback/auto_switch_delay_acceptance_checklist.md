# 自动切歌延迟修复 — 验收清单

执行人：影子澪
来源文档：`docs/spec/playback/auto_switch_delay_analysis.md`

---

## 验收范围

本清单覆盖自动切歌延迟问题的修复验证，按场景分为三个维度：
**埋点验证 / 行为验证 / 指标验收**。

---

## 一、埋点验证

确认 `[measure]` 日志各节点存在且数据符合预期：

| # | 埋点 | 预期 |
|---|---|---|
| M1 | `timer_fired` | 定时器触发时输出 |
| M2 | `before_group_force_stop_xiaoai` | 进入 stop 前输出 |
| M3 | `after_group_force_stop_xiaoai` | stop 完成后输出，`dt` 应在 1.5~2.5s（fast stop） |
| M4 | `before_group_player_play` | 进入 play 前输出 |
| M5 | `after_group_player_play` | play 完成后输出，`dt` 应在 1.0~3.0s |
| M6 | `after_confirm_playback_started` | confirm 完成时输出（非阻塞路径，应晚于主链很多） |
| M7 | `group_stop_dispatch ... wait_mode=overlap` | 自动切歌已切到 overlap 模式 |
| M8 | `play_start_confirmation_attempted ... retries=0` | 自动切歌后台确认已切到轻量参数 |
| M9 | `status_playing_observed dt_from_timer=...` | 设备进入 playing 时输出，距 timer_fired 应在 2.6~3.9s |

---

## 二、行为验证

在真实设备上跑自动切歌，验证以下场景：

### 2.1 单设备自动切歌
- **预期**：切歌流畅，无明显停顿或失败
- **验证方法**：播放一首正常时长歌曲（>30s），等待自动切到下一首，重复 3 轮

### 2.2 组播设备自动切歌
- **预期**：组内所有设备同步切歌，无设备掉队或不同步
- **验证方法**：有组内多设备时执行，同样等待自动切歌 3 轮

### 2.3 手动下一首
- **预期**：手动触发切歌的行为和耗时与自动切歌一致
- **验证方法**：手动点下一首，观察 `[measure]` 日志主链耗时

### 2.4 播放失败后的自动补救
- **预期**：起播失败时进入补救路径，不静默卡死
- **验证方法**：触发一首可能失败的歌曲（无效 URL 或网络异常），观察日志中是否出现 fallback 或失败处理路径

### 2.5 Jellyfin / proxy fallback 路径
- **预期**：Jellyfin 直接 URL 失败时，proxy fallback 能正常触发
- **验证方法**：使用 Jellyfin 直链歌曲触发一次切歌，观察日志中是否有 proxy 重试

---

## 三、指标验收

### 3.1 主链完成时间
- **指标**：`timer_fired -> after_group_player_play` 的 `dt`
- **目标**：稳定落在 **1.7s ~ 1.9s** 区间
- **注意**：超过 4s 视为异常，需要排查

### 3.2 设备 playing 时间
- **指标**：`timer_fired -> status_playing_observed` 的 `dt_from_timer`
- **目标**：大致落在 **2.6s ~ 3.9s** 区间
- **注意**：这是设备真正进入 playing 的近似时间，比主链 API 完成晚约 0.5~2s

### 3.3 起播成功率
- **指标**：连续 5 轮自动切歌中，成功进入 playing 的比例
- **目标**：≥ 80% 为可接受，≥ 95% 为理想
- **注意**：出现 `started=false` 或 fallback 时记录次数

### 3.4 副作用检查
- **检查项**：
  - 重叠播放（两首歌同时出声）
  - 尾音未停就切下一首
  - stop 失效导致下一首拒绝播放
  - 切歌后当前曲目标题仍然残留
- **出现任一现象**：记录具体情况，标注为"需排查"

---

## 四、配置确认（运行时检查）

| 配置项 | 预期值 |
|---|---|
| `auto_next_stop_wait_mode` | `overlap` |
| `auto_next_stop_grace_ms` | `200` |
| `auto_next_confirm_delay_ms` | `800` |
| `auto_next_confirm_retries` | `0` |
| `auto_next_confirm_interval_ms` | `300` |
| `play_url_mode` | `auto`（保持 `play_by_music_url`） |
| `delay_sec` | 用户设定值（确认生效即可） |

---

## 五、不验收事项（明确排除）

以下项本轮不作为验收目标，不在此清单范围内：
- delay_sec 的提前/延后语义验证
- play_by_url vs play_by_music_url 的重新对照
- 手动播放同步确认路径的参数调整
- 极短定时器（<1s）连续切歌的边界条件

---

## 验收结论格式

每项完成后在清单内打 `[x]`，发现问题时附上：
- 日志片段或截图
- 具体时间戳
- 设备 ID
- 异常描述

最终输出一句结论：**通过 / 有条件通过（附问题列表） / 不通过**