# nonebot-plugin-game-event-due

二游活动日历：**每天中午检查一次，只在 24 小时内有活动要开始或要结束时推一条**。
只发文字，数据源是 [SRA 公共 API](https://starrailassistant.top/api/v1/activity/)。

> 由 AstrBot / Koishi 同名插件重写而来，见 [来源](#来源)。本分支是 NoneBot2 实现。

## 它做什么

每天 12:00 检查一遍订阅的游戏，把**未来 24 小时内**要开始或要结束的活动，
按方向分组、合并成**一条**消息发给群：

```
🎮 明日方舟：终末地
▎即将结束（6）
· 「丰碑留名·魇林」限时挑战　11-05 04:00
· 「危机筹备」限时活动　11-05 04:00
· 「万物有灵」签到活动　11-05 11:59
· 「墨分诸彩」引入活动　11-05 12:00
▎即将开始（4）
· 「危机合约 浸蓝测试作战」挑战活动　11-05 12:00~11-26 05:59
· 「归野」叙事活动　11-05 12:00~11-26 05:59
· 「星河共我」签到活动　11-05 12:00~11-26 05:59
· 「狂热赛季」（战争回响）　11-05 12:00~11-26 05:59
```

没有命中就**一个字都不发**。所以平时很安静，只在活动交接日出现一条。

## 特性

- 支持 18 款游戏：明日方舟、终末地、星铁、原神、绝区零、鸣潮、异环、蔚蓝档案三服、
  星塔旅人、无限暖暖、少女前线2、战双帕弥什、卡厄斯梦境、棕色尘埃2、NIKKE、火影忍者
- **每群每天最多一条**，多个游戏、多个方向全部合并
- 每行只有「标题 + 时间」，标题行不带窗口字样，行尾不带「开始/结束」
- 一个活动在其生命周期内最多被提醒两次：开始前一次、结束前一次
- 兼容 OneBot v11 与 QQ 官方 Bot（QQ 适配器为可选依赖，不硬 import）
- 插件自身不暴露任何群内指令

## 安装

```bash
# 推荐：带定时任务与缓存目录管理
pip install nonebot-plugin-game-event-due[scheduler,localstore]
```

在 `pyproject.toml` 里加载：

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_game_event_due"]
```

或直接把目录丢进 `extra_plugins/`（`plugin_dirs = ["extra_plugins"]` 会自动发现）。
缺 `nonebot_plugin_apscheduler` 时插件仍能加载，只是不会有任何定时推送。

## 指令

**没有。** 订阅与推送范围完全由管理员在配置文件里决定，群成员无法改动。
历史指令（`/活动订阅`、`/活动查询`、`/活动阈值` …）的代码完整保留在
`events/commands.py` 的注释块里，需要恢复时解开对应片段即可。

## 配置

环境变量前缀 `GAME_EVENT_DUE_`，嵌套用双下划线，例如
`GAME_EVENT_DUE_DAILY_PUSH__HOUR=12`。

### 检查节奏

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `daily_push.hour` / `minute` | `12` / `0` | **检查时刻**（东八区）。一天只跑这一轮 |
| `deadline_push.thresholds` | `"3,1"` | 结束方向窗口（天），`1` = 24 小时 |
| `start_push.thresholds` | `"1"` | 开始方向窗口（天），`1` = 24 小时 |
| `quiet_hours` | `"23-8"` | 静默时段；命中但处于此时段就推迟到时段结束 |

`daily_push.enabled` 只管「全量摘要」那条，**不影响检查时刻**——即使关掉摘要，
12:00 的检查照常进行。

### 订阅

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `preset_subscriptions` | `""` | 唯一的订阅入口，格式见下 |
| `deadline_push.enabled` | `true` | 全局是否启用结束提醒 |
| `start_push.enabled` | `false` | 全局是否启用开始提醒（建议用预置订阅按群开） |
| `notify_new_activity` | `true` | 新活动出现时是否提醒 |
| `notify_on_subscribe` | `true` | 下发订阅后是否立刻推一次概览 |

### 数据源与缓存

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `sra_api_base` | SRA 地址 | 数据源基址 |
| `request_timeout` | `20` | 请求超时（秒） |
| `proxy` | `""` | 代理，留空不走代理 |
| `cache_dir` | `""` | 缓存目录；留空用 localstore，再退化为包内 `.cache` |
| `fetch_interval_hours` | `8` | 手动刷新时的缓存最大年龄（天级检查会强制取数，不受它限制） |
| `refresh_on_start` | `true` | 启动时预热一次缓存 |

### 消息格式

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `single_message_per_game` | `true` | 合并成一条（关掉会按游戏分别发送） |
| `activity_separator` | `━━━━…` | 活动之间的分隔线；想更紧凑可设为空 |
| `urgency_marker` | `true` | 紧急度色块 |
| `sort_by_urgency` | `true` | 同一分组内按时间由近到远排序 |
| `insert_cover` | `true` | 有版本主视觉就配一张图，没有就纯文字 |
| `cover_width` | `640` | 配图宽度（px），只在 QQ 官方 Bot 的 markdown 里生效 |
| `game_aliases` | `""` | 自定义显示名，如 `sr=星铁,ak=舟游` |

## 图文混排

**有图插图，无图文字**——不需要额外开关，取决于数据源给不给得出图。

配图来源是 SRA 游戏 JSON 的**根级 `cover`**（版本主视觉）。活动级的
`cover` 绝大多数是同一张图重复挂（实测终末地 14 个活动里 9 个挂同一张），
所以插件只按游戏取一张：根级有就用根级，没有就退而取活动里出现次数最多
的那张，一张都没有就纯文字。

两个平台的做法不一样，插件在投递层消化掉差异：

| 平台 | 图片怎么发 |
| --- | --- |
| OneBot v11 | 图片以 `[CQ:image,file=<url>]` 内联，文字与图在同一条消息 |
| QQ 官方 Bot | 先把图上传到素材库换成 `qqbot-media://…`，再用 markdown（`msg_type=2`）内联 |

QQ 官方那条路要三步（`prepare` → `PUT` → `complete`），同一张图按内容
md5 只上传一次，结果缓存在 `material_cache.json`。任何一步失败都**自动降级
为纯文字**，图片退化成「封面：<链接>」——绝不因为配图挂了而丢掉整条推送。

实际渲染（2026-11-04 真机数据）：

```
### 明日方舟：终末地

![封面 #640px](qqbot-media://image/v1/...)

**▎即将结束（6）**

- 「丰碑留名·魇林」限时挑战　**11-05 04:00**
- 「卡莉的芳菲童话」拍照活动　**11-05 04:00**
...
```

想关掉图片：`GAME_EVENT_DUE_INSERT_COVER=false`。

## 预置订阅

```dotenv
GAME_EVENT_DUE_PRESET_SUBSCRIPTIONS=qq:group:0123456789ABCDEF=方舟,终末地@1~1
```

格式 `会话key=游戏列表@结束档位~开始档位`：

| 写法 | 含义 |
| --- | --- |
| `@1` | 只在活动**结束前**不足 1 天时提醒 |
| `~1` | 只在活动**开始前**不足 1 天时提醒 |
| `@1~1` | 两个方向都要（推荐） |
| `@7,3,1~1` | 结束方向 7/3/1 天三个档位 |
| `@~` | 两个方向都关闭 |
| 全省略 | 沿用全局 `deadline_push` / `start_push` |

- `会话key` 形如 `qq:group:<群 openid>` / `onebot:group:<群号>`；
- 游戏支持代号与中文别名（`ak`/`方舟`、`end`/`终末地`、`sr`/`星铁` …）；
- 多条用换行或 `;` 分隔；私聊会话会被跳过并告警；
- 同一份配置**只应用一次**（按内容签名判断），之后重启不会覆盖群里的设置；
  想重新下发，改动 `preset_subscriptions` 这一行内容即可。

新活动出现时不会补推历史活动：首次运行时会把当时在册的活动标记为已知，
避免一次性收到几十条。

## 推送规则

| 类型 | 触发条件 | 默认 |
| --- | --- | --- |
| 开始提醒 | 活动**开始前**不足 24 小时 | 按订阅开启 |
| 结束提醒 | 活动**结束前**不足 24 小时 | 开启 |
| 新活动提醒 | 出现新活动（`notify_new_activity`） | 开启 |

几条边界行为：

- **窗口是闭区间**：恰好剩 `24:00:00` 算进入窗口，多一秒就不算。这条必须闭，
  否则「明天 12:00 开始」的活动会掉进今天和明天两轮之间的缝隙里。
- **当天不会再发一次**：12:00 开始的活动，当天那轮检查时它已经开始
  （`is_upcoming` 不成立），加上档位去重，不会重复推送。
- **去重按活动维度**记录，推送成功后立即落盘；发送失败则不写，下一轮重试。
- 每条消息的长度受 QQ 平台限制，超长建议设 `activity_separator` 为空或关掉 `urgency_marker`。

## 缓存行为

| 场景 | 行为 |
| --- | --- |
| 每天 12:00 检查 | 强制向上游取数 |
| 插件启动 | 缓存仍在磁盘上，立即可用；`refresh_on_start` 决定是否立刻预热 |
| 上游故障 | 回退到磁盘缓存并在日志里标注 |

## 部署示例

```dotenv
GAME_EVENT_DUE_ENABLED=true
GAME_EVENT_DUE_DAILY_PUSH__ENABLED=false      # 不要全量摘要
GAME_EVENT_DUE_DAILY_PUSH__HOUR=12            # 每天 12:00 检查
GAME_EVENT_DUE_DEADLINE_PUSH__THRESHOLDS=1    # 结束前 24 小时
GAME_EVENT_DUE_QUIET_HOURS=23-8
GAME_EVENT_DUE_ACTIVITY_SEPARATOR=
GAME_EVENT_DUE_INSERT_COVER=true              # 有版本主视觉就配图
GAME_EVENT_DUE_PRESET_SUBSCRIPTIONS=qq:group:<群openid>=方舟,终末地@1~1
```

## 已知限制

- 配图只有**每个游戏一张版本主视觉**，没有活动级独立插画（数据源里就没有）
- 卡池（`pools` 字段）暂不解析
- 不支持多轮补充查询：一天只有一次检查，活动若在两次检查之间开始并结束，
  只能在下一次检查时补发（实际数据里活动时长都远超 24 小时，不会遇到）
- 上游数据由 SRA 社区人工维护，时间以官方公告为准

## 开发

```bash
python -m pytest -q      # 174 passed
python -m ruff check .
python scripts/preview_rich.py   # 用真实 SRA 数据预览纯文本 / CQ 码 / markdown 三种形态
```

目录：`events/polling.py`（调度与投递）、`events/services/reminder_service.py`
（档位与去重）、`events/services/message_formatter.py`（消息格式）、
`events/sources/sra.py`（数据源）、`platform/media.py`（QQ 素材上传）、
`platform/push.py`（图文混排与降级）、`platform/capability.py`（平台能力）。

## 来源

重写自以下社区插件的功能集合，本仓库是其中的 NoneBot2 分支：

- [XinDuKW/astrbot_plugin_game_event_due](https://github.com/XinDuKW/astrbot_plugin_game_event_due) — 活动起止时间聚合与分级提醒（主要来源）
- [zhewang448/astrbot_plugin_gameinfo](https://github.com/zhewang448/astrbot_plugin_gameinfo) — 游戏信息与日历聚合
- [wxgl/astrbot_plugin_arkinfo](https://github.com/wxgl/astrbot_plugin_arkinfo) — 明日方舟资讯
- [yabo083/koishi-plugin-miyako-intel](https://github.com/yabo083/koishi-plugin-miyako-intel) — Koishi 侧的情报聚合

## 许可

MIT
