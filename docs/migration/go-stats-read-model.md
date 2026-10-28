# 第三阶段：Go SC 用户统计读模型验收

日期：2026-10-28。基点 `9068ad269b6fa23a5d954f3903b490dd7e3bf8d3`，分支 `refactor/multi-work-dlc`。开工已核对HEAD及Git状态，已跟踪工作区干净，原scratch、.DS_Store、docs/ideas等未跟踪资料保留。本轮规格、单票与原始日志在`.scratch/go-stats-read-model/`；仅本地合成数据，未访问生产。

## 已实现的职责变化

显式 `-stats-token-file` 启用后，Go实际接管 `GET /api/stats` 的 days_known、当前/最长连续访问、24小时分布、52整周加本周热图，以及all/30d/1y内的互动/通话/礼物计数和角色顺序投影。好感分组复用已验收的Go affinity.Compute。测试故意让Python公开聚合函数抛错，Go仍成功，证明不是转发算好的公开统计。

Python保留原Cookie会话/同意条款/封禁鉴权、可信用户与SC范围、Range校验、profile/device/default有效时区选择、所有SQLite访问和成就tally/shown。已追踪 `happenings.tally → Database.read → achievements.tally`：当前只读，不初始化、不结算、不发奖；本轮没有翻译成就规则或增加业务写入。直连Python旧stats原样保留；Go未启用时仍是明确的旧代理模式，启用后合同或上游失败返回502，不静默降级。

没有Schema v3变更、迁移、Go直连SQLite、非SC玩法或公开新接口。好感写入、礼物、任务、奖励及其他业务仍在Python，本切片不代表整个第三阶段完成。

## 私有合同与身份

新增隐藏OpenAPI的 `GET /api/_core/stats`。复用原 `EIDOLON_CORE_SERVICE_TOKEN_FILE`，不另造认证体系或复用用户key；`core_service_auth`明确命名其现在保护好感及统计两类私有只读能力，保留旧affinity_service_auth别名供兼容。Go `-stats-token-file`独立于 `-affinity-token-file`，使用相同owner-only、64位小写hex凭据格式。未配置Python私有入口404；配置损坏启动失败。凭据用途扩展仅到这两类读模型，部署启用及密钥分发仍需单独审查。

私有入口要求服务凭据和原用户Cookie，同时复用sc_request。客户端user_id不选择读取用户，非SC/冲突声明拒绝；Go公共 `/api/_core` 所有方法无条件封锁。新统计测试、既有命名空间测试与三进程均验证封锁。向固定Python origin请求，不跟重定向、不使用客户端Authorization作服务身份；转发Cookie、Origin、X-Work-ID、X-Timezone和原query，保留多Set-Cookie。无共享用户缓存/CookieJar。

NDJSON v1按顺序发送：

- meta：version、work=sc、已验证span、单次可信观察时间、注册时间、开关和原成就结果。
- character：按可信偶像顺序，每角色一条ID和可空原始好感状态；SQL一次读取而非逐角色HTTP/SQL。
- first：原Store最早保留记录的时间与角色索引。
- contact：角色索引、turn/gift、时间与当时liked；call：角色索引、连接时间和原duration。
- end：必需完整终止；缺失、异常记录、尾随数据均失败，不返回部分公开结果。

不含对话正文、记忆、用户ID或密钥，不将原始统计写入日志。已移除/导入的未知角色使用各自不同负数索引，不传其ID，仍参与总量、最早日期和礼物最大值；公开列表/并列选择仍只按可信目录顺序。若未知角色独占最多礼物，原接口的most=null保持。

## 日期、规模和一致性边界

Python用同一次解析的ZoneInfo把各历史时间规范为当地带偏移ISO时间；Go按其中民用日期/小时计数，日历范围不按固定24小时回推。支持DST重复/跳过小时、历史秒级UTC偏移，避免两份IANA数据库版本不同导致结果漂移。好感使用同一个观察时刻的UTC瞬间计算。Python只提供时间元数据，不预计算计数或档位。

私有传输每条记录上限64KiB，仅含固定小字段和单个可信角色ID，不随历史记录总数增大；Python按约32KiB批次输出，Go逐条聚合，无总响应长度限制或4KiB截断。合法接近单行上限与超限失败均有测试。Go状态为O(不同角色数)加固定公历1..9999日期位集（约457KiB）和至多371天热图，不保留逐条事件。历史中不同未知角色的计数同样占状态，不能声称全部为常量内存。

不设置成功流整体截止时间，沿用旧代理对长读取的行为；下游取消传入上游并关闭响应。上游永久停滞而客户端始终不取消仍可能长期占连接，是明确剩余限制，不以人为小超时改变旧合法历史语义。Python仍沿用原Store列表物化、分次读取顺序和原成就读取；不是Python常量内存，也不是新增事务快照。并发删除/写入期间仍可能出现原接口已有的跨次读取不一致。

## 失败、修复与独立审查

| 证据日志 | 实际结果与处理 |
|---|---|
| model-red.log | Go Compute未实现，编译失败；model-green.log随后通过独立worked example。 |
| snapshot-red.log | 私有入口不存在，非SC请求得到404而非预期409；实现后snapshot-green.log的快照/好感4项通过。 |
| http-red.log | 新HTTP处理器未实现，编译失败；http-green.log实际传输/取消通过。 |
| contract-red.log | nullable成就计数被JSON解为零值，测试真实失败；显式非null校验后通过。 |
| cancel-fault.log | 仅在Docker副本将上游上下文换为Background，真实取消测试2.008秒失败且完成清理；8秒整进程超时未触发。随后恢复源码并重新构建。清理与取消观察通道分离，不会制造通过信号。 |
| static-first.log | 1个未使用测试import；删除后static-second.log通过ruff/pyright。 |
| 422-leak-red.log | Spec独立审查发现P2：合法422对象带raw_stats额外键被原样公开。真实HTTP红灯复现后，要求固定五字段非null并对detail/ctx严格白名单；两层额外字段均502。合法401/422保留。 |

Standards独立审查：无硬规范问题或P1/P2，一项可选P3为统计与好感差分测试的进程生命周期夹具重复。本票记录并暂不抽取，避免为维护性建议改动已接受的好感测试；不是遗漏回归或未解决安全缺陷。Spec修复复核关闭唯一P2，两轴最终均无未解决P1/P2。

## 本轮实际测试与构建

| 范围 | 真实结果 | 日志 |
|---|---|---|
| Go全包/TCP、race、vet、build | 全通过；EIDOLON_TCP_TESTS=1，最终统计5个Go测试不使用缓存重跑 | go-final.log |
| Python主要相关11文件 | **57 passed、12 warnings**，33.59秒 | python-related-first.log |
| 受影响补充16文件 | **203 passed、27 warnings**，16.39秒 | python-affected.log |
| 最终Go二进制跨语言/三进程5文件 | **8 passed、2 warnings**，24.13秒；是前述测试子集，不重复累计 | python-final-chain.log |
| Python全src/tests静态 | ruff通过；pyright **0 errors、0 warnings** | static-second.log |

两批共 **260个不同测试通过，0 failed、0 skipped**。警告为现有依赖弃用提示，分别保留每次运行统计。提交前 `affected-tests.sh --list` 的文件清单在affected-list.log，其全部测试在上述Docker运行中覆盖；静态检查也在Docker运行。ruff只排除保留在容器中的旧诊断探针test_state_reward_audit_probe.py（非仓库正式测试）。未跳过正式失败测试或改变业务预期。

最终差分覆盖：15组时区/范围组合、profile优先、非法range、重复range、伪造他人user_id、不同用户、非SC、开关、原始聚合故障注入、未知角色最大礼物、五万条额外历史、删除后统计与未登录。SQLite合成业务表快照确认读统计/拒绝非法作品没有改动库存、奖励、好感/冷却、任务、成就、会话/事件等。50,000额外互动与原14条合计50,014，完整对比Go与Python结果；最终两次HTTP和对比合计 **0.299秒**，仅为本地单次观测，不承诺性能提升。

真实Next生产包 → 本轮Go → Uvicorn测试包含两种assets配置；原迁移历史/跨作品及跨用户拒绝、记忆/R1/R2、OAuth/Cookie/Origin/SSE和新增统计保留。SSE首段 **52.532 / 54.281 ms**，模型取消 **6.510 / 9.063 ms**；Go独立统计读取实际上游取消 **29.833µs**。取消观察阈值2秒用于发现传播缺失，故障注入证明会失败，没有扩大窗口变绿。

本轮未重跑Python全量，也未重复前端单元、类型或构建：前端未改，三进程复用既有R2生产包。历史全量数字不混入本轮结果。未执行任何生产数据/迁移/部署验收。

实际命令（均在现有Docker工作目录）：

```sh
# /work/core
EIDOLON_TCP_TESTS=1 go test -count=1 -v ./internal/stats
EIDOLON_TCP_TESTS=1 go test ./...
EIDOLON_TCP_TESTS=1 go test -race ./...
go vet ./...
go build -o /work/eidolon-core-stats ./cmd/eidolon-core
# /work/backend；三进程/差分按需设置下列路径
# EIDOLON_STATS_BINARY=/work/eidolon-core-stats
# EIDOLON_CORE_BINARY=/work/eidolon-core-stats
# EIDOLON_AFFINITY_BINARY=/work/eidolon-core-stats
# EIDOLON_NEXT_SERVER=/work/r2-next-standalone/server.js
.venv/bin/pytest -q -s tests/test_go_stats.py tests/test_stats_snapshot.py tests/test_three_process.py tests/test_stats.py tests/test_call_stats.py tests/test_affinity_stats.py tests/test_achievements_stats.py tests/test_go_affinity.py tests/test_affinity_snapshot.py tests/test_sc_play_scope.py tests/test_grant_idempotency.py
.venv/bin/pytest -q tests/test_activity_rollups.py tests/test_call.py tests/test_embedding_config.py tests/test_health.py tests/test_llm_config.py tests/test_live_config.py tests/test_live_config_limits.py tests/test_live_config_services.py tests/test_affinity.py tests/test_affinity_cooldown.py tests/test_affinity_prompt.py tests/test_affinity_turns.py tests/test_affinity_dashboard.py tests/test_sessions.py tests/test_work_memories.py tests/test_go_catalog.py
.venv/bin/pytest -q -s tests/test_go_stats.py tests/test_stats_snapshot.py tests/test_three_process.py tests/test_go_affinity.py tests/test_affinity_snapshot.py
.venv/bin/ruff check src tests --extend-exclude tests/test_state_reward_audit_probe.py
.venv/bin/pyright --pythonpath /work/backend/.venv/bin/python
```

## 最终源码、资源与风险

source-match.log确认Python src/tests与本地一致（仅排除__pycache__与旧诊断探针），Go cmd/internal/go.mod/go.sum与本地一致。两个容器实际验收二进制摘要均为 `aca7e946d4581f6a0198bbcf701e5229828f4b3bfc900c46ad28df3748e8889c`，每份10,867,609 bytes。

没有新增镜像、容器、卷、依赖安装、网络发布或宿主机工具。只启动并复用既有Python/Go容器；Node容器保持原停止状态，Next进程在Python容器内运行。期末可写层（resources-before/after.log）：

| 容器 | 开工bytes | 期末bytes | 本轮差额bytes |
|---|---:|---:|---:|
| Python | 2,648,305,664 | 2,679,877,632 | +31,571,968 |
| Go | 555,225,088 | 579,985,408 | +24,760,320 |

合计新增可写层 **56,332,288 bytes，约53.72 MiB**，含新二进制、Go缓存、合成SQLite/凭据临时目录和源码副本；不是镜像拉取体积。宿主机新增本票源码/文档与scratch证据，scratch核对时224KiB，后续报告/票据收尾少量增加；未在宿主机编译或安装依赖。没有测量实际服务内存峰值，固定日期位集不代表总RSS。长历史Python物化、生产并发与凭据分发/轮换仍需后续单独验证。

不自动删除资源。验收后停止本轮启动的两个容器，保留所有历史产物及缓存。日后确认不再需要后，可单独删除两容器的/work/eidolon-core-stats、本轮Docker测试源码备份/work/stats-http-clean.go和合成临时目录、宿主机.scratch/go-stats-read-model；不要用全局prune或删除整个复用容器/卷来清理本票。

本切片已实现并本地验证Go统计投影；生产启用、真实用户数据容量验证未做，不能据此批准生产部署。下一条业务迁移需单独授权，本轮到此停止。
