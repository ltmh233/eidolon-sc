# 第三阶段：Go SC 对话列表与最近摘要验收

日期：2026-10-28。分支refactor/multi-work-dlc，基点`d065576cdb304a814fd2df8c9aae3f63abdfb797`。开工已核对实际HEAD、Git状态及源码，已跟踪工作区干净，原scratch/.DS_Store/docs/ideas等资料保留。本票规格、单票、探针与日志在`.scratch/go-conversation-summaries/`。全部使用本地既有Docker、合成库及凭据，不是生产验收或迁移许可。

## 已迁移与保留责任

显式`-summaries-token-file`启用后，Go实际负责原`GET /api/conversations?character_id=...`和`GET /api/conversations/latest`的最后记录选择、预览及公开摘要组装。它接收原索引查询各取一条的message/event/reach_out及必要比较元数据，不是转发Python.last_shown结果。故障注入原Python `_last`后，两路Go仍200。

选择保持原规则：事件候选排除ended；event/reach_out取时间最大的，同时间reach_out优先；仅当pending时间>=最后user时间（无user时为最后message时间）才覆盖message。消息自身仍由原SQL按ID选择。空记录null；message保留role/text/ja/zh/time，photo仅ready时true；reach_out化为角色消息并标记reach_out:true；event只公开type/kind/time及可选礼物ID/可信名称。原消息/事件/搭话ID、photo标签/ID、affinity、liked及私有错误/比较字段不进入预览。

Python继续原用户/SC索引查询、会话created_at DESC,rowid DESC顺序、每角色最新会话选择、全部SQLite及标题任务、原delivery cues过滤。未知/移除角色按旧结果返回，不新增404；缺character_id保留原422，空值/重复参数按原FastAPI规则。旧Python公开接口保留，未启用Go标志时仍旧代理，启用失败不回退。Schema v3、创建/删除/回合/通话/奖励不改，非SC未开放，前端协议未改。

## 查询、标题与副作用

Store仅拆分 `_last_candidates` 和 `_latest_conversation_objects` 供原路径及私有路径共用。原SQL的user/SC/character谓词、ORDER BY、LIMIT 1均保留；没有为了Go排序读取全部消息/事件，也没有新增每会话HTTP。原列表每会话数次索引点查仍存在，本票不声称消除其既有SQL N+1。latest仍按原方式读取该用户SC会话元数据、在Python保留每角色第一条，未改成Go全历史筛选。

列表先await原`titles.settle(user_id)`，最多原5秒，之后才读取候选；latest不等待。测试使用真实后台标题模型gate：latest和其他用户列表在gate未放行时完成；本用户列表进入原settle后确实等待，放行后返回数据库中真实写入的标题。没有新标题任务，也不把等待改成Go计算或自动重试。

两路均不调用read_parts_by_user，不标搭话已读。合成SQLite业务表快照、自己与他人的seen状态在正常读取、伪造身份、非法作用域后保持；标题后台任务自己完成的原写入是明确保留行为。原始候选只来自认证用户及SC视图；客户端user_id/work_id不选择读取身份。

## 私有合同、错误与资源

隐藏`/api/_core/conversation-summaries`和`.../latest`复用现有core凭据、用户Cookie及sc_request，公共Go整个/api/_core命名空间继续封锁。凭据权限明确包括摘要候选，不能当成一般用户API key。固定私有origin，一次HTTP流，无共享用户缓存/日志正文；沿用已验收的禁keep-alive/HTTP2私有GET，避免复用连接失败后隐式重读/重复等待，不改变公共GET Origin语义。

NDJSON v1：meta(version/work/mode/列表需要的本用户ID，latest为null)、row(会话基础元数据、ended或unseen、三类候选及比较键)、end。公开列表原本已有的id/user_id保留；latest不带user_id/title，原始记录ID只用于内部，不公开。完整end+EOF之后才发送200，长列表/大预览无新64KiB或总响应字节截断；复用请求内随机密钥AES-GCM加密spool，不回到明文临时文件。内存仍需最大row和加密块索引，Python仍物化原候选列表；不是固定内存或生产容量保证。

保留多Set-Cookie、认证错误及准确的缺参数422。422只接受固定missing/query/character_id错误形状，私有extra字段502，不原样泄漏。未知礼物只有被选中才保留原标准500；退休但仍登记的礼物照旧显示。cues预处理失败也仅在Go选中该记录时返回原500；未选中的坏候选不提前导致失败。私有异常错误不暴露内部记录/路径。

## 历史时间 P2 与必要共享修复

Spec独立审查发现：原Python及SC离线迁移都接受`datetime.fromisoformat`范围，Go RFC3339Nano不等价。合法空格分隔会502，超过六位的小数Python截到微秒、Go纳秒可能改变同时间选择。history-time-red.log以真实SQLite复现200对502。

修复为共享`Store.comparison_order`：Python按原解析器提供精确整数微秒与aware标记，Go仍比较/选择，公开created_at始终原文。用无时区年月日差减utcoffset计算，避免边界日期强转UTC溢出，不把naive猜成服务器时区。摘要只在原逻辑需要比较的时点规范键；原解析错误/不可比较的aware-naive混合保持标准500。

补查确认同类问题也在基点完整时间线：保留旧二进制SHA`5aac5ab33d0e31d5de3918c303e260582981beabbab8d34b5b5f12dc169d0666`的合成探针200对502（baseline-history-iso-probe.log），正式历史回归再次红灯（shared-history-time-red.log）。因此只补齐必要共享比较兼容，没有扩大业务迁移：新Go历史请求`X-Core-History-Comparison: python-us-v1`，Python在协商时附加私有comparison_order并回能力header；新Go要求能力匹配且每记录有合法键，否则502，不混用旧解析。旧Go不请求仍得原记录形状，旧Core规范时间/大记录兼容探针1passed。比较键不进入公开JSON，原SQL/已读水位方法不变。

边界：非法坏时间文本不属于SC迁移接受的数据；裸Python有时会在无需比较时原样展示，而基点Go已会拒绝。本票没有猜修复异常库或承诺所有非法数据等价。真实生产数据及容量未验证，不把合成库成功当作生产迁移许可。

## 红灯与独立复核

| 日志 | 实际结果 |
|---|---|
| model-red.log / snapshot-red.log | 新Go函数未实现编译失败、私有能力不存在404；随后worked example和私有候选通过。 |
| unselected-cues-red.log | 旧候选预处理使未选中的坏segments导致Go502、Python200；display_error只在选中时生效后通过。 |
| history-time-red.log | 合法历史时间比较不兼容，后按Python精确微秒键修复。 |
| baseline-history-iso-probe.log / shared-history-time-red.log | 基点旧历史二进制及正式历史测试均确认同类缺陷；当前新二进制修复后通过。 |
| cancel-fault-confirmed.log | Docker副本同时切断HTTP与投影上下文，摘要取消测试2.008秒真实失败并清理完成，未触发8秒整进程超时；之后恢复源码。 |

Standards无硬规范/P1P2，可选P3重复解析候选改为一次后复核关闭。Spec时间P2、共享历史修复及能力协商复核关闭，无新增P1/P2。最终额外去除历史记录重复JSON解析，正常/异常检查语义不变，之后重新Go全套与实际摘要/历史/三进程。两轴为真实源码/证据复核，未伪报其另跑测试。

执行环境曾出现一次Docker命令自动权限审核超时，测试未执行；允许的一次重试成功。未将审核超时写成测试失败，未退到宿主机安装依赖/构建。

## 本轮实际验收

| 范围 | 结果 | 日志 |
|---|---|---|
| 原相关26文件 | 176 passed、5 warnings，40.33秒 | python-primary.log |
| 最终共享修复后26文件 | **176 passed、5 warnings**，37.94秒，替代同批历史结果 | python-primary-final.log |
| 受影响补充13文件 | **226 passed、27 warnings**，21.67秒 | python-affected.log |
| 最后Go二进制摘要/历史/三进程3文件 | **5 passed、2 warnings**，7.42秒，是上述子集 | python-final-binary.log |
| 旧Core兼容独立探针 | **1 passed、2 warnings**，1.39秒，不纳入正式测试总数 | old-core-compat.log |
| Go专项/全包/TCP、race、vet、build | 全通过；12个timeline测试不使用缓存重跑 | go-final.log |
| Python全src/tests静态 | ruff通过；pyright **0 errors、0 warnings** | static-shared-final.log |

合计 **402个不同正式Python相关测试通过，0 failed、0 skipped**；重复批次和5项子集不重复累计。红灯/基点失败日志保留，旧二进制边界失败不冒充当前失败。警告为现有依赖弃用提示，按各批真实值记录。affected-list.log的19个文件全部覆盖；ruff仅排除容器保留的旧诊断探针test_state_reward_audit_probe.py，非仓库正式测试。未重跑Python全量或前端单元/类型/构建；前端未改，真实三进程复用原R2生产包。

最终实际观测：250条额外会话及 **3,900,000 bytes大预览**完整JSON差分合计 **0.103秒**；完整时间线3.9MB单条+一万事件差分 **0.805秒**。三进程SSE首段 **50.675 / 54.915 ms**，模型取消 **4.373 / 5.850 ms**，摘要上游实际取消 **30.25µs**。只是本地单次观测，不承诺性能提升。2秒取消窗口用于发现传播缺失，故障注入证明确实失败，清理通道不制造成功，未扩大窗口变绿。

真实Next→新Go→Uvicorn继续覆盖R1/R2、好感、统计、完整时间线、迁移历史/记忆、OAuth/Cookie/Origin和SSE；新增列表/latest完整对比及实际私有路径日志证明两路启用。没有连接生产服务或真实用户数据库。

## 一致性、资源和限制

source-match-final.log确认Python src/tests及Go cmd/internal/go.mod/go.sum与本地一致（仅排除__pycache__和旧容器诊断探针）。两容器最终二进制均为 **11,038,858 bytes**，SHA-256 `2ce5cc9ed3aceced797a499a350138a908cc998ca73f443c18a1a1b9dabc03a1`。

未新增镜像、容器、卷、依赖、网络发布或宿主机工具；仅启动复用既有Python/Go容器，Node容器未启动。resources-before.log与resources-after-final.log可写层净值：

| 容器 | 开工bytes | 期末bytes | 净差bytes |
|---|---:|---:|---:|
| Python | 2,662,563,840 | 2,696,708,096 | +34,144,256 |
| Go | 617,459,712 | 667,684,864 | +50,225,152 |

合计净差 **+84,369,408 bytes，约80.46MiB**，含二进制、缓存和框架合成临时目录生命周期，不是毛新增产物体积。宿主机只新增源码、文档和本票scratch（核对时292KiB，收尾少量增加），没有宿主机构建文件。独立新探针的容器副本运行后移除，源码/日志留scratch；未删除既有用户文件、scratch或Docker资源。

密钥只在请求内存，密文缓冲的保护不包括RAM零化、进程内存转储或含内存快照。未测服务内存峰值、生产长列表/最大单条与并发容量；Python原候选物化、既有索引点查、最长5秒标题等待和每次私有新连接仍是明确成本。匹配服务版本/凭据分发与生产启用须另审。

收尾只停止本轮启动的两个容器，不清理资源。日后审查后可单独移除容器/work/eidolon-core-summaries、/work/summaries-http-clean.go及合成临时目录，宿主机本票scratch；请求匿名加密文件自动关闭，不是删除用户文件。禁止为本票全局prune或删除整个复用容器/卷。

本票仅本地源码/合成验收，不宣称第三阶段整体完成；未部署、推送或开始下一切片。

### 复验命令

在Docker `/work/core`：

```sh
EIDOLON_TCP_TESTS=1 go test -count=1 -v ./internal/timeline
EIDOLON_TCP_TESTS=1 go test ./...
EIDOLON_TCP_TESTS=1 go test -race ./...
go vet ./...
go build -o /work/eidolon-core-summaries ./cmd/eidolon-core
```

在Docker `/work/backend`，设置EIDOLON_SUMMARIES_BINARY、EIDOLON_TIMELINE_BINARY、EIDOLON_STATS_BINARY、EIDOLON_CORE_BINARY、EIDOLON_AFFINITY_BINARY均为`/work/eidolon-core-summaries`，EIDOLON_NEXT_SERVER为`/work/r2-next-standalone/server.js`：

```sh
.venv/bin/pytest -q -s tests/test_summary_snapshot.py tests/test_go_summaries.py tests/test_three_process.py tests/test_go_timeline.py tests/test_timeline_snapshot.py tests/test_go_stats.py tests/test_go_affinity.py tests/test_affinity_snapshot.py tests/test_stats_snapshot.py tests/test_conversation_titles.py tests/test_past_conversations.py tests/test_conversation_events.py tests/test_conversation_history.py tests/test_unseen_reach_outs.py tests/test_reach_outs.py tests/test_reach_outs_in_turns.py tests/test_photo_records.py tests/test_photos.py tests/test_photo_failures.py tests/test_call_photos.py tests/test_gift_giving.py tests/test_data_package_conversations.py tests/test_data_package_reach_outs.py tests/test_work_conversations.py tests/test_store_sessions.py tests/test_delivery_cues.py
.venv/bin/pytest -q tests/test_call.py tests/test_call_turns.py tests/test_embedding_config.py tests/test_health.py tests/test_live_config.py tests/test_live_config_limits.py tests/test_live_config_services.py tests/test_llm_config.py tests/test_memories.py tests/test_retrieval_services.py tests/test_turn_outcome.py tests/test_turns.py tests/test_work_memories.py
.venv/bin/pytest -q -s tests/test_go_summaries.py tests/test_go_timeline.py tests/test_three_process.py
.venv/bin/ruff check src tests --extend-exclude tests/test_state_reward_audit_probe.py
.venv/bin/pyright --pythonpath /work/backend/.venv/bin/python
```
