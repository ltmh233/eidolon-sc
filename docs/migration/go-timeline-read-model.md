# 第三阶段：Go SC 历史时间线投影验收

日期：2026-10-28。分支refactor/multi-work-dlc，基点`28e34d499754770a40210e0588b598d9df8b00ed`。开工HEAD/状态已核对，已跟踪工作区干净，用户原scratch、.DS_Store、docs/ideas等保留。本票规格、单票及全部原始日志在`.scratch/go-timeline-read-model/`。仅合成数据、本地Docker验收，不是生产迁移或部署许可。

## 已实现与保留责任

显式启用`-timeline-token-file`后，Go实际接管原`GET /api/conversations/{id}/messages`的Message、ConversationEvent、ReachOut合并及公开字段投影。消息按ID排列；事件/搭话按时间、同时间事件优先、再按ID排序；仅在user消息前插入严格早于它的pending记录，不把回复途中事件插入user/reply之间。不会把所有记录简单按created_at排序。无记录返回`[]`，保持原数组及可选字段/空值。

Go隐藏photo.tag和事件liked，构造照片id/status/reason、gift_id/gift_name以及三类记录的原字段。原照片响应没有URL字段，前端原取图协议不变。退休但仍登记礼物照旧显示；未知礼物的旧KeyError/HTTP500语义保留为标准500文本，不泄漏私有unknown_gift标记。

Python继续认证Cookie/同意条款/封禁、可信SC会话视图及用户归属、角色存在校验、全部SQLite查询/写入、复杂delivery cues过滤和可信礼物名称查找。没有把Python.shown结果原样套JSON：私有能力提供分开的dataclass记录，Go真实组装；故障注入使原Message/Event/ReachOut.shown全部抛错，Go仍成功。Python直连旧公开GET保留；未启用Go标志仍为旧代理，启用错误不降级。没有Schema v3变更、Go直连SQLite、非SC开放或创建/删除/发言/通话/奖励迁移。

## 已读水位与访问隔离

原路径为`read_by_user → _record_and_unseen → _see_reach_outs(up_to)`；不是纯只读。新`read_parts_by_user`复用相同SQL读取次序、_unseen_watermark和原写函数，只将合并留给Go。先读消息/事件/搭话并捕获本次最大搭话ID，再由Python单写者仅更新`id<=up_to`；没有先标已读再读、Go补写或新自动结算。

受控测试在本次读取和写已读之间插入更大ID，返回仅旧搭话且seen为`[True,False]`。不同用户、Demo会话、不存在会话、移除角色在Go及直连私有FastAPI均404，外来/移除角色搭话保持未读；非法作品声明409且本会话也未被标读。客户端user_id不选择身份。

授权读取已经完成后，即使后续投影/网络失败，捕获的搭话仍可能已读；这与原公开路由先read_by_user、再shown的顺序一致，不是新增送达ACK协议，也不声称失败一律没有副作用。只有拒绝访问路径要求已读零副作用。

## 私有合同与禁止重读

隐藏`GET /api/_core/conversations/{id}/messages`复用core服务凭据+原用户Cookie，并校验SC及可信角色。既有EIDOLON_CORE_SERVICE_TOKEN_FILE现在授权好感、统计和敏感会话历史三类私有能力，部署用途扩展需另行审查。客户端Authorization不作为服务身份。固定私有origin，不跟重定向、不共享缓存或CookieJar，保留所有Set-Cookie及原401/403/404/409/428详情。公共Go的整个/api/_core命名空间仍封锁。

NDJSON v1：meta(version/work/conversation)、分开的message/event/reach_out记录、event的可信礼物名、最后end。结构错误、缺end、尾随数据/读取失败不发布部分200。数据含敏感正文，仅经固定认证链路传输，不写业务日志、共享缓存或错误详情。

先尝试私有POST来避免隐式GET重试，但它触发原Origin保护，使合法不带Origin的公共GET返回403；differential-first.log保留失败，不绕过Origin。最终私有GET使用独立克隆的HTTP/1传输，禁连接复用和HTTP2，防止net/http在复用连接错误后自动重读。真实故障先暖连接，再在第二次读取后断连：默认传输会第三次读取并返回200（retry-red.log），修复后第二次502、总读两次。无应用重试，不转发客户端Idempotency-Key。

## 大消息、完整性与隐私缓冲

TurnInput.text没有小长度限制；数据包data上限200MiB，SQLite还有自身行长度约束。因此不照搬统计64KiB记录上限，不设总历史字节截断。Go逐记录解析，保留记录/加密块索引和最大记录的内存，仍需容纳最大单条；Python仍物化原列表。不是全程常量内存，也未验证任意生产容量。

Go先把各公开投影写入请求私有缓冲，完整end+EOF后按索引组装完整数组到第二缓冲，再发200及Content-Length。后者不是边读上游边对外发部分数组。临时文件0600且立即unlink；**仅unlink不能真删**，独立审查因此提出P2，已修复为两处均只写AES-256-GCM密文：每文件随机请求内密钥，块nonce唯一计数、AAD绑定明文偏移，随机读取先认证后解密。64KiB是加密块大小，不限制消息。写失败/短写锁存，后续Write不能复用失败nonce。

密钥不落盘，Close关闭句柄并丢引用，保护磁盘释放块及仅磁盘快照中的正文副本；不保证RAM零化、进程内存转储或含密钥的内存快照安全。没有新增密钥服务、队列、分页服务或数据库。缓冲磁盘不足等错误返回502，但原已读写入不会回滚；这是读取提交后交付失败的既有语义。未模拟真实整机磁盘满，只用本测试匿名文件故障验证写失败锁存。

## 先失败后修复与审查

| 证据 | 真实结果 |
|---|---|
| model-red.log / http-red.log | 未实现的Go能力编译失败，随后模型/HTTP回归通过。 |
| snapshot-red.log | 私有能力不存在404，未能返回原始记录；实现后水位与旧事件/已读11项通过。 |
| differential-first.log | 私有POST导致403，方案放弃；GET修复后differential-second.log两项通过。 |
| retry-red.log | 默认GET隐式重试：上游读取3次，第二公共请求200而非502。独立禁复用传输修复后通过。 |
| photo-contract-red.log | 异常照片字段可被当作公开对象穿透；改为类型投影，异常不发布输出。 |
| plaintext-spool-red.log | 从实际底层FD读到明文，不能用目录为空代替安全；改为加密文件后底层无明文、跨块/顺序读取和篡改拒绝通过。 |
| spool-write-red.log | 审查P3：模拟写失败再恢复，未来重试会复用nonce；错误锁存后拒绝后续写入。 |
| cancel-fault-incomplete.log | 仅切断HTTP上下文时，投影取消仍可关闭body，测试通过；这不算故障命中。 |
| cancel-fault-confirmed.log | 同时切断HTTP及投影上下文，测试2.017秒失败并完成清理；8秒整进程超时未触发。日志中rg缺失只影响源码打印，不影响该实际失败；使用现有工具，无安装。之后恢复源文件。 |

独立Standards：一项循环上游断言缺场景名P3，已仅加c.name、逻辑不变；复核无新硬规范问题/P1/P2。独立Spec：明文临时副本P2已关闭；防御性写失败锁存P3也已先红后绿关闭。最终无未解决P1/P2。审查均为源码及证据核对，不伪报额外测试。

## 实际验收

| 本轮运行 | 结果 | 日志 |
|---|---|---|
| Python时间线/水位/照片/礼物/导入/隔离/原始三进程25文件 | **172 passed、4 warnings**，38.03秒 | python-primary.log |
| 受影响补充13文件 | **226 passed、27 warnings**，21.81秒 | python-affected.log |
| 加密缓冲二进制差分/三进程等7文件 | **10 passed、2 warnings**，24.56秒，前两批子集 | python-final-chain.log |
| 最终锁存版二进制大型时间线及三进程 | **3 passed、2 warnings**，6.68秒，前述子集 | python-final-binary.log |
| Python静态全src/tests | ruff通过，pyright **0 errors、0 warnings** | static-first.log |
| Go最终专项、全包/TCP、race、vet、build | 全通过；8个timeline测试不使用缓存重跑 | go-final.log |

合计 **398个不同Python相关测试通过，0 failed、0 skipped**；10和3都是子集，不重复计数。依赖弃用警告按每批原始值保留。受影响清单affected-list.log中的20文件全部覆盖，再加真实API间接相关测试。ruff仅排除容器保留的旧诊断探针test_state_reward_audit_probe.py，非仓库正式测试。未重跑Python全量或前端单元/类型/构建；前端无修改，Next复用已验收R2生产包。

最终测试比较完整JSON：**3,900,000 bytes单条UTF-8消息加10,000条事件**，两次HTTP和差分合计 **0.805秒**。同时间混合、回复成对、原cues、已结束历史、ready/pending/failed照片、退休/未知礼物及用户/作品/已读路径均覆盖。真实Next→新Go→Uvicorn保留R1/R2/好感/统计/记忆/历史/SSE断言，并从实际私有请求日志确认Go历史路径启用。

最终三进程SSE首段 **51.307 / 77.072 ms**，模型取消 **5.630 / 5.137 ms**；Go独立时间线流取消 **19.958µs**。只是本地单次测量，不承诺提升。取消观察2秒用于检测传播缺失，故障注入证明确实失败，清理通道不会伪造成功；未扩大窗口变绿。

实际命令：Go在/work/core执行EIDOLON_TCP_TESTS=1的go test -count=1 -v ./internal/timeline、go test ./...、go test -race ./...，及go vet ./...、go build -o /work/eidolon-core-timeline ./cmd/eidolon-core。Python在/work/backend运行上述38文件（清单和分批见对应日志/affected-list），最终二进制路径由EIDOLON_TIMELINE_BINARY、EIDOLON_STATS_BINARY、EIDOLON_CORE_BINARY、EIDOLON_AFFINITY_BINARY指向/work/eidolon-core-timeline，EIDOLON_NEXT_SERVER=/work/r2-next-standalone/server.js。静态为`.venv/bin/ruff check src tests --extend-exclude tests/test_state_reward_audit_probe.py`及`.venv/bin/pyright --pythonpath /work/backend/.venv/bin/python`。所有依赖、编译及测试均在已有Docker，未在宿主机安装工具或构建。

## 源码一致、资源与限制

source-match.log核对Python src/tests、Go cmd/internal/go.mod/go.sum与本地一致（仅排除__pycache__和容器旧诊断探针）。两处实际最终二进制均为10,900,097 bytes，SHA-256 `5aac5ab33d0e31d5de3918c303e260582981beabbab8d34b5b5f12dc169d0666`。

未新增镜像、容器、卷、依赖或网络发布；复用两个既有Go/Python容器，Node容器未启动。期末可写层resources-before/after.log：

| 容器 | 开工bytes | 期末bytes | 净差bytes |
|---|---:|---:|---:|
| Python | 2,679,877,632 | 2,662,563,840 | -17,313,792 |
| Go | 579,985,408 | 617,459,712 | +37,474,304 |

合计净差 **+20,160,512 bytes，约19.23MiB**。这是整个复用可写层净值，包含测试临时文件生命周期及缓存变化，不能据此推算本轮毛新增量；Python为负也不代表本轮未创建产物。未手工清理用户文件/卷或历史证据。新增两份timeline二进制、Go缓存与合成测试目录；宿主机只增源码、文档、本票scratch（核对时152KiB，收尾会增加），没有额外构建文件。没有测量实际服务内存峰值；最大单条内存、缓冲磁盘空间、加密CPU、每请求新连接及生产并发容量仍需后续单独验收。

验收后停止本轮启动的两个容器，不删除资源。日后审查后可单独移除容器内/work/eidolon-core-timeline、/work/timeline-http-clean.go及本票合成临时目录，宿主机本票scratch；请求匿名加密文件由其生命周期自动关闭，不是用户文件清理。禁止全局prune或为本票删除整个复用容器/卷。

本切片已实现并本地验证；Schema、写入者及其余业务边界不变。生产启用/服务凭据分发、真实用户容量验证未执行，不能据此批准生产操作。下一部分另行授权，本票收尾即停止。

### Python分批复验清单

在上文Docker环境及二进制变量下：

```sh
.venv/bin/pytest -q -s tests/test_timeline_snapshot.py tests/test_go_timeline.py tests/test_three_process.py tests/test_go_affinity.py tests/test_affinity_snapshot.py tests/test_go_stats.py tests/test_stats_snapshot.py tests/test_unseen_reach_outs.py tests/test_reach_outs.py tests/test_reach_outs_in_turns.py tests/test_conversation_events.py tests/test_conversation_history.py tests/test_past_conversations.py tests/test_delete_past_conversations.py tests/test_photo_records.py tests/test_photos.py tests/test_photo_failures.py tests/test_call_photos.py tests/test_gift_giving.py tests/test_data_package_conversations.py tests/test_data_package_reach_outs.py tests/test_data_package_photos.py tests/test_work_conversations.py tests/test_store_sessions.py tests/test_delivery_cues.py
.venv/bin/pytest -q tests/test_call.py tests/test_call_turns.py tests/test_embedding_config.py tests/test_health.py tests/test_live_config.py tests/test_live_config_limits.py tests/test_live_config_services.py tests/test_llm_config.py tests/test_memories.py tests/test_retrieval_services.py tests/test_turn_outcome.py tests/test_turns.py tests/test_work_memories.py
.venv/bin/pytest -q -s tests/test_timeline_snapshot.py tests/test_go_timeline.py tests/test_three_process.py tests/test_go_stats.py tests/test_go_affinity.py tests/test_stats_snapshot.py tests/test_affinity_snapshot.py
.venv/bin/pytest -q -s tests/test_go_timeline.py tests/test_three_process.py
```
