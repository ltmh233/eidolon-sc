# Go SC character achievement read model

This slice moves the complete `GET /api/characters/{character_id}/achievements` projection to Go when `-achievements-token-file` is configured. Without that flag the public Python route remains the rollback path. It does not enable achievements for another work, migrate the gifts panel, or change the schema or reward transactions.

Python remains the only SQLite reader/writer. The existing core read credential now also permits `/api/_core/achievements/{character_id}`; it still requires the real user's session, SC scope and a current Python character. The credential uses the existing owner-only 64 lowercase hex file format and Python `EIDOLON_CORE_SERVICE_TOKEN_FILE`. SC creation credentials must remain different, including deployments enabling only creation and achievements. This document does not enable or distribute credentials.

The dedicated private GET captures current ordered rules and raw SQL scalars for just that user and character. INTEGER uses signed decimal strings, REAL uses exact binary64 bits, and TEXT/BLOB retain their distinct representations. Python parses every saved stage date with its original ISO parser and supplies normalized ISO plus exact comparison microseconds. It supplies no projected progress, completion decision or reset-card display value. The disabled stream contains only disabled metadata and its terminator and reads neither achievement table.

Go validates the versioned NDJSON arrays, counts, duplicate identities, normalized dates, mandatory terminator and EOF before publishing a response. It then computes current stage coverage, the conditional stable maximum timestamp, selected Python-compatible integer conversions, tier indexing and the historical completion marker display. Counts do not unlock stages; a `complete` row suppresses the displayed card even when its stored count is zero or newly added stages are missing. Go never awards that card or calls back into a Python reward transaction.

The numeric profile is explicitly `py311-sql-v1`, Python 3.11 with Unicode 14 decimal digits/whitespace and the runtime decimal-digit limit. Integer output preserves values beyond binary64 and signed64. Unknown old counts are not converted, but every old stage date must parse. Required aware/naive times only fail when full current coverage actually requires a mixed maximum; equal instants retain the first stage in definition order.

Private legacy date/SQLite failures use the exact `sc-character-achievements-v1` / `legacy_read` envelope before successful headers. Go maps that envelope and valid-snapshot projection failures to the old standard 500 body. Unsupported profiles/rules, damaged streams, unexpected upstream errors and an old Python without this endpoint become generic 502. A configured request makes one fresh HTTP/1 request with redirects and connection reuse disabled; there is no automatic retry, capability probe or fallback. Original authentication errors and all Set-Cookie headers are preserved.

Deploy Python first, then opt into the Go flag. Old Core continues to use Python's original public route. New Core with old Python fails 502; disable/roll back the Core flag before rolling Python back. The public Go router always refuses `/api/_core`. Direct Python must remain on its existing private network boundary.

Both processes retain this request's metadata and result in memory. There are no new plaintext snapshot files, spools or small record limits. Memory grows with rule and saved-key counts; this is not a constant-memory or production-capacity claim. Authentication renewal and process startup may still write independently of this read endpoint. Existing reward, ledger, launch/backfill and reset-card grant transactions remain Python-owned.

## Local validation

The implementation evidence is in `.scratch/go-character-achievements/logs/`. The initial public worked example first compared the original Python result to a complete literal, then disabled Python's old `hers`/`Hers.shown` only during the gateway request. Preserved old Core, using its supported affinity flag and the achievements proxy path, returned 500 where the unchanged public expectation was 200. The native achievements flag subsequently passed that same expectation under the same fault window.

Formal tests cover the pure decoder/Compute boundary, public Go HTTP and TCP responses, authenticated raw adapter, independent original Python SQL oracle, version combinations and the fixed existing Next → Go → FastAPI chain. Final command outcomes, binary/source hashes, resource measurements and any exclusions are recorded with the implementation evidence; a skipped opt-in test is not acceptance. No host language dependency installation, frontend build, real user database, schema migration, deployment, commit or push is part of this validation.

## 2026-10-29 本轮实际验收结果

基点/当前HEAD为`2ea146de95aefd9de94f8742f168aadb77faadb2`，分支`refactor/multi-work-dlc`。当前15个正式文件（6源码、7测试/夹具、2文档）留作待审diff；未暂存、提交、push、PR或部署。精确清单见`.scratch/go-character-achievements/final-manifest.md`。

本轮不是整个第三阶段验收，也不是生产迁移许可。没有改动Store、SQLite schema、Happenings/achievements写入与奖励账本、玩法经济/活动生命周期、原始记忆/实时/前端逻辑。新增私有GET只读能力已获本票专门授权，仍需原服务凭据及真实会话，不能替代用户认证。

### 红、绿与故障证据

日志根目录为`.scratch/go-character-achievements/logs/`，序号保留执行先后；失败日志没有覆盖。命令及环境变量完整记录于相邻`execution-commands.md`。

| 日志 | 本轮实际结果 |
|---|---|
| 01 / 03 | 完整公开worked example先红：旧Core以其已有flag保持代理，禁用Python旧投影时实际500、预期200，**1 failed**；新native flag下同一期望绿，**1 passed**。不是缺flag/编译失败。 |
| 04 / 07 | oracle扩展首轮夹具在恢复D后遗留超长TEXT，1 failed/1 passed；修正夹具后4 passed。没有修改生产数字预期或限制历史数据。 |
| 08 / 13 | 早期全Go unit（部分cache）/全包race通过；后续修复又重跑相应检查，不把早期代码当最终。 |
| 11 / 16 | 三项测试类型错误（异常形状构造、query容器类型）已修，后续pyright零错误。 |
| 12 / 17 / 20 | 先125 passed/2 failed：三进程子进程从旧/work helper加载，seed预期不符；改用/source cwd后暴露测试seed与已有launch行冲突（17：5 passed/2 failed），用合成夹具ON CONFLICT更新后20：18 passed。业务不修历史、不改鉴权或放宽断言。 |
| 14 / 15 | 独立审查P2：畸形401错误正文绕过Unicode验证，实际401而预期502；局部修复后通过，合法surrogate pair及字面反斜线仍保留。 |
| 18 / 19 | 独立审查P2：D=4300时4301位cards/goal/draws规则应拒绝却接受；修后按D边界、负号不计数、D=0不限全部通过。仅规则wire限制，原SQL计数仍lazy。 |
| 21 / 22 / 23 | 容器中的错误Go二进制分别破坏截断、tier和同刻max选择，真实公开差分均exit1；最终源和产物已恢复，不能把负向失败计作普通通过。 |
| 24 / 25 | 真实TCP上游Context取消正测通过；故意切断取消传播，**2.01秒预期失败且清理退出**。独立cleanup不关闭observed通道，没有扩大2秒观察窗使其变绿。 |
| 26 | 最终Go全部9个有测试包`EIDOLON_TCP_TESTS=1 go test -race -count=1 ./...`通过，随后`go vet ./...`、`go build -trimpath`成功。cmd包无测试不计入。 |
| 27 / 30 | 真实旧Python与并发奖励边界首轮2 failed/2 passed，原因仅测试Cookie域、POST Origin；修夹具后4 passed，不改生产认证。 |
| **32** | **唯一24文件最终集合：255 passed，0 failed，0 skipped，25 warnings，34.71秒**。6个affected清单文件全部包含，另含必要的成就/奖励事务/R1R2/记忆/账号/删除/导入回归。不是Python全量，不与上述各组相加。 |
| 34 / 41 | 对/source实际源码和测试`ruff check --no-cache`通过，`pyright --pythonpath /work/backend/.venv/bin/python`零错误/警告；41为主代理最后确认。 |
| 38 | 最后P3只给Go两条路径失败提示补path，不改断言逻辑；成就包含TCP单独重跑通过、vet通过、gofmt无差异。没有改生产代码或重建不必要的前端。 |

最终24文件集合详见`execution-commands.md`；包括test_achievement_snapshot、test_go_achievements、call/health、成就各回归、daily_tasks、gift_giving/draws、reset_cards/grants、sc_play_scope、go_conversation_create、three_process、work_schema/conversations/memories、delete_account/past_conversations、data_package_conversations、sessions和data_access。

### 完整模型及真实边界

最终SQL oracle报告**35个场景**，不是35个pytest项：原Python hers+shown与新Go完整公开JSON对照。包括>2^53精确INTEGER、最大REAL向零截断、有效/非法TEXT/BLOB、Unicode14 Nd/25空白与D=0、负tier索引、旧stage键数值相等、未知旧数据、目标变更、marker存在且count=0、全量日期解析、仅完整所需阶段max的混合aware错误、同刻按定义首遇ISO。未使用坏count不转换，计数高不补阶段或奖；关闭时不解析坏日期。原业务表前后快照、其他用户及显式DLC拒绝已验证。

真实版本组合：保留的旧Core+新Python、new Core关闭native均继续原公开读取；new Core+新Python走完整原始协议；new Core+基点`app.py`创建的实际旧应用，其旧公开GET200而新private路径404，最终Go通用502，观察到只有一次私有请求、无探测/回退。旧应用使用同一合成backend及未改模块，不声称测试了另一套历史依赖版本。早期404 stub不作为最终真实组合证据。

原生读取可用、私有读取失败、native禁用三模式下，真实两个并发回合仍只解锁并发放一次阶段奖、一次完成卡；与只读GET并行时账本不重复。privileged read并未进入或回调奖励事务；重复GET无业务写。原SC缺省/显式sc、未知/移除角色404、关闭null、用户隔离与原Cookie/Origin边界保持。完整HTTP测试还验证多Set-Cookie、拒绝重定向、精确信封、私有路径封锁、单次读取及取消；服务与创建凭据相同仍拒绝启动（含只配置本新read flag的场景）。

固定现有生产Next包→新Go→FastAPI分别运行两个assets_url场景，合成DB中的成就17进度等完整响应与直连一致，读取不发卡/奖；同时保留记忆、目录、创建、删除、R1/R2、OAuth/Origin、SSE兼容。最终32日志SSE首段为**48.442 / 46.837 ms**，模型上游实际取消**3.632 / 3.512 ms**。这些是本地合成观察值，不是SLO，未重新构建或声称前端全量通过。

### 独立审查与受测来源

Standards初审两类P3（重复输入行、循环提示不足）及末次同类路径提示遗漏全部修复并独立复核关闭；Spec两项P2（错误信封Unicode、规则D位数）全部先红后绿且独立复核关闭，无未解决P1/P2。见`.scratch/go-character-achievements/review-final.md`。这里只修改实现必要内容，没有顺手扩大到全局JSON/RPC或Python模拟框架。

13份运行时代码/正式测试与实际受测来源逐项匹配：Go的/work/core副本与只读/source逐字cmp，Pythonapp/adapter及子进程helper实际从/source加载。最后P3测试文件的哈希取39/42日志，覆盖37早期快照；README/本报告是文档不作为运行时SHA。复核结果在43-source-artifact-verification.md。

- 新Go `/work/eidolon-core-achievements`（11,083,672 bytes）SHA256：`e77451b674f32980f7078ea6cc3cb191ad6022e9229bcae40393562d183f34e6`，两个容器副本一致。
- 旧Go `/work/eidolon-core-visible-text` SHA256：`f0aaa7d9c2ea52488ffaa0b085e95f70f20de9c053a5fe95190e9da71e501e4a`，未覆盖。
- 固定Next server SHA256：`3a44d862ae978d8ebe4195c385c5820e04ba413be37d6e5ebe75ffa4a3a7227c`；其编译代理route SHA256：`0113206246f62f07f1c31eacf30883c5f55ac1c8152ececadf0883bd4d596eae`，均未改。

### 资源、未跑项及限制

只复用`eidolon-accept-p2-go-20261024`与`eidolon-accept-python-20261024`，RO项目挂载，无宿主端口、Docker Socket/HOME/凭据挂载，无新镜像/容器/卷/依赖安装。Go编译缓存、新/故障二进制和合成测试目录保留在既有容器；没有宿主构建或临时中转二进制。

33日志只是在测试后空闲时的容器样本：Go约1008KiB、Python约8.457MiB，**不是运行峰值**。未采样本轮Go/后台进程运行时RSS峰值，未做大规模键数/并发容量验收。内存算法保留原始记录/键索引及结果，随规则和数据增长；不能据此承诺生产内存预算。磁盘净变化与最终停止状态见收尾追加及resources日志，不与上轮磁盘累计混算。

未跑Python全部测试、新前端测试/typecheck/build、真实模型/用户库、生产环境或升级迁移；原semantic-oracle探针是规格准备的Python行为证据，不算本轮Go测试通过。32日志中的期望故障traceback及DataAccess队列/checkpoint测试向已关闭测试输出写日志的Logging error原样保留，pytest最终exit0；未对后者做独立归因或修改未变化的DataAccess，不能称“日志全程无异常”。

当前支持的数值profile仅受审Python3.11/Unicode14，其他profile明确不可用；SC遗留成就表仍无work_id，本票不解决其他作品的存储。生产启用、容量验证和下一切片均需另外审查，不因本地功能通过自动开展。

### 最终资源与Git收尾

本轮启动的两个既有容器均已停止，未删除，最终状态和挂载见`logs/44-resources-final-stopped.log`。没有新镜像、容器、卷、宿主端口或依赖安装。

| 既有容器 | 实施前可写层 bytes | 收尾可写层 bytes | 本轮净增 bytes |
|---|---:|---:|---:|
| eidolon-accept-p2-go-20261024 | 899,915,776 | 947,847,168 | 47,931,392 |
| eidolon-accept-python-20261024 | 3,436,441,600 | 3,564,244,992 | 127,803,392 |
| 合计 | 4,336,357,376 | 4,512,092,160 | **175,734,784（167.59375 MiB）** |

这是Docker可写层净增，包含编译缓存、故障二进制及合成测试目录，不是macOS物理磁盘占用或峰值。宿主仅增加/修改本票源码、测试、文档和`.scratch/go-character-achievements/`证据；收尾后该scratch的`du -sk`为928KiB（早一轮收据前为924KiB）。未生成宿主构建、数据库或依赖目录。

没有执行清理。如后续需回收，应先审阅该scratch中的命令和测试临时目录清单，只删除本票的`/work/eidolon-core-achievements*`产物及已确认的合成测试目录；既有共享容器、编译缓存和其他切片产物不能整体删除。scratch日志/基点app/探针须由维护者另行决定保留期限；本轮全部保留。

最终HEAD仍为上述基点，暂存区为空，`git diff --check`通过；15个正式文件清单与实际改动一致，原有`.DS_Store`、其他scratch和私人资料保留。实现票本地验收完成，交协调方审查diff；没有暂存或创建提交，也没有push、PR、部署或开始下一切片。

### 协调复核接受与本地提交范围

上述待审记录保留。后续协调方已只读复核生产源码、主要测试、报告和日志，以42清单逐项核对13份源码/测试一致；独立Standards与Spec均无新增阻塞问题。协调方接受255项最终相关回归、Go全包race/TCP、vet/build及Python静态证据，其复核未重跑测试。内存峰值、生产容量及未跑全量的限制继续保留。

本次接受仅授权上述15个正式文件的本地提交：6源码、7测试/夹具、README与本报告。scratch/日志、密钥、数据库、构建产物和私人资料均排除；不push、不创建PR、不部署。提交SHA及提交后精确文件核对单独保存在本票scratch收据，避免在提交中写入自身SHA。下一部分仅准备礼物/每日任务/全局成就面板的规格，未授权实施。
