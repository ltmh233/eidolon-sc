# SC 原生 Go 链路：本地拓扑与资源包络验收

2026-10-30实施、2026-10-31收尾（Asia/Shanghai；原日志时间保留），票 `.scratch/go-migration-stage-close/issues/01-supported-topology-and-capacity.md`，基点/最终HEAD均为 `36f105cf87040d54a98ed75af50ce3c9421a6720`，分支 `refactor/multi-work-dlc`。本报告记录本票实际执行；上一票 440/37 等历史结果不计入本票。

**本地实验完成：S/M指定稳定场景在固定预算内通过；L单并发/4并发完成，8并发首轮预热触发Go真实OOM，后续安全停止，不能声明L完整支持。** 交付待审diff及完整失败证据，不是生产启用许可，也不代表整个多作品平台完成。本票没有更改业务、Schema、权限、生产 Compose、10 秒 idle / 60 秒总期 / 同一 1 秒清理边界，也没有暂存、提交、推送、PR 或部署。

## 可复现入口

正式入口是 `scripts/stage-acceptance.sh`，测试胶水位于 `backend/tests/stage_acceptance.py`、`stage_capacity.py`、`stage_faults.py`、`stage_summary.py`。需要本机已有 Docker 镜像与以下**精确复制的既有产物**；无需宿主安装语言运行时或安装依赖。

本轮复制来源是停止的 `eidolon-accept-python-20261024` 中 `/work/backend/.venv`、`/work/eidolon-core-gifts-panel`、`/work/r2-next-standalone`，以及停止的 `eidolon-accept-node-20261024` 中 `/usr/local/bin/node`。只复制这几个路径，没有 snapshot 整个容器。全部保存在 `/private/tmp/eidolon-stage-close-20261030-artifacts/{venv,core,next,node}`。版本兼容定向回归额外精确复制了旧 Go `/work/eidolon-core-achievements` 和已有合成 baseline 源码，未运行旧容器。

```sh
export ARTIFACTS=/private/tmp/eidolon-stage-close-20261030-artifacts
# 每次必须使用新的 run 名称；日志、SQLite、basetemp、资源均保留。
sh scripts/stage-acceptance.sh start loopback S example-loop
sh scripts/stage-acceptance.sh functional example-loop
sh scripts/stage-acceptance.sh receipt example-loop
sh scripts/stage-acceptance.sh stop example-loop

sh scripts/stage-acceptance.sh start bridge S example-s
sh scripts/stage-acceptance.sh functional example-s
sh scripts/stage-acceptance.sh capacity example-s
sh scripts/stage-acceptance.sh receipt example-s
docker exec eidolon-stage-close-20261030-example-s-load \
  /work/backend/.venv/bin/python -m tests.stage_summary /run
sh scripts/stage-acceptance.sh stop example-s

# 仅 S 上跑一次定点故障；不与容量矩阵做笛卡尔积。
STAGE_FAULTS=1 sh scripts/stage-acceptance.sh start bridge S example-fault
sh scripts/stage-acceptance.sh functional example-fault
sh scripts/stage-acceptance.sh faults example-fault
sh scripts/stage-acceptance.sh receipt example-fault
sh scripts/stage-acceptance.sh stop example-fault
```

S/M/L 串行，每档启动都会检查没有任何运行容器，再用独立 32 MiB 探针检查 Docker VM 实际 MemAvailable 和磁盘可用空间。最终入口要求同实例 `functional.exit=0`、无安全停止标记、四角色生命周期无OOM，拒绝复用 `capacity.jsonl`；实时OOM事件在批次间阻断继续。所有 pytest 都预检唯一不存在的 `--basetemp`，日志另存 actual inner exit；不使用默认临时目录，不覆盖首次失败。容量失败后先读收据分类，不能仅把任意 public502 标为过载。原始证据都在 `.scratch/go-migration-stage-close/<run>/` 与 `logs/`，根代理收尾证据在 `root-logs/`。

最终入口另有实时阶段门：host Docker CLI按本票唯一容器名订阅OOM，经FIFO把真实事件写入共享`oom-stop`；不向任何容器挂Docker socket。功能期即使pytest exit0，只要出现OOM，整体入口exit86且禁止capacity。容量每个预热/稳定/历史/混合批次前后检查同一marker，遇到OOM停止剩余和更高格；observer断线或operator中断也失败关闭，分别标记，不能冒称OOM。公共cleanup在正常/EXIT/INT/TERM时写当前sampler sentinel、wait自有采样PID后关闭watcher；信号先写operator-interrupt，使可能仍存活的driver也在下一批拒绝继续。

## 拓扑和配置

两种实际运行拓扑均为合成客户端 → 固定生产 Next standalone → Go → 单进程、单 worker Uvicorn/Python → 合成 SQLite。Go/Python 私有腿是直接 HTTP/1.1，支持同时 chunked 上传和响应。没有缓冲代理、H2 私有腿、自动重试或重定向。SQLite 仍只由 Python 原写者写入，Go 无业务数据库连接。

| 拓扑 | 实际运行 | 已证明的边界 |
|---|---|---|
| loopback | `loop-s-03`：四个角色独立 cgroup；Go/Next/load 使用 `--network container:<本票Python>`，Python `--network none` | 完整公开 API/原 Python oracle/SQL 后态；不是旧一个容器三进程历史结果。真实停止 Python 后，Next 的 SC 原生目录仍 200/29 角色，动态面板 502 |
| 独立 bridge | `bridge-s-03`：独立服务容器与内部 bridge，实际 DNS `next` → `go` → `python` | 全 native flags 组合的完整功能；S 稳定矩阵；故障/其余档位见结果表 |
| 未测/不支持 | 多 Python worker、缓冲代理、私有 H2、外层生产代理/TLS、多主机 | 不能从本地直接 HTTP1 结果外推支持 |

全部 read、SC create、gifts-panel 凭据由本次 release 离线生成，内容互不相同、0600；公开 `/api/_core` 路由拒绝。内容 v3 快照与 display 通过当前 Go 导出/原 Python 离线物化并绑定摘要。每个 run 有 `release.json`。启用 affinity、stats、timeline、summaries、SC create、achievements、完整 gifts panel；目录 SC/demo 是可信本地测试包，非 SC 聊天仍拒绝。

公开链路通过真实假 OAuth 提供者建立 Cookie，验证多 Set-Cookie、Origin、consent、R1 作用域/用户边界、R2 持久幂等结果、历史记忆召回、SC 创建/删除及完整面板 11 字段。原 Python oracle 比完整响应和 14 张业务表后态，不以状态200或 mock 计数代替。测试跨服务 DNS 的 oracle 使用显式复制的**合成** domainless Cookie；这只用于测试直连 Python/Go，生产浏览器 Cookie 语义没有变化。

## 固定预算与输入

Docker 29.8.2，Linux aarch64，10 CPU，VM MemTotal 12,599,537,664 B。每组入场要求实际 MemAvailable 减去组预算后仍有 max(1 GiB, MemTotal/4)=3,149,884,416 B 余量。完整组 2,248,146,944 B。每档 admission 和 disk-before 文件保留实际值；未调 Docker 系统设置，没有借用/重启/删除旧用户容器。

| 角色 | memory = memory+swap 总上限 | CPU quota |
|---|---:|---:|
| Go | 96 MiB，GOMEMLIMIT=64 MiB | 2 |
| Python | 1 GiB | 2 |
| Next | 512 MiB | 1 |
| 合成 load | 512 MiB | 1 |

模型、embedding、reranker 为 Python 测试夹具内本地替代器，内存记入 Python 角色；额外 load 单独受限。没有真实外部模型调用。辅助 release 准备容器 512 MiB/1 CPU 在测量前退出，不与测量组叠加。没有 HOME、SSH、Docker socket、privileged、host network 或宿主端口挂载/发布。

seed 标识 `20261030`，无 PRNG 的顺序枚举。详见规格 §9 和 `data-manifest.json`，独立 literal 测试冻结字节/分布。S/M/L 分别新增 100/10,000/100,000 唯一历史退役键，偶数 `turns`、奇数 `tier`，`character_id=retired_%06d`，count=i%3，last_day=NULL，均为合法 SQL 值。实际初始原库还含 4 条 mano 和 1 条 launch 产生的全局 days，因此初始总候选 N+5；最初 N+4 的估计已据真实 SQL 改正，样本未修改。当前 SC 目录仍 29 角色。

| 档位 | 新增行 | 规范三元组 JSONL B | 实际私有 count 记录预定 UTF8 B |
|---|---:|---:|---:|
| S | 100 | 2,850 | 5,650 |
| M | 10,000 | 285,000 | 565,000 |
| L | 100,000 | 2,850,000 | 5,650,000 |

这些只含新增 count 记录，不含基线记录、阶段头、nonce 或 HTTP chunk framing。`private-wire.json` 从真实 socket 记录逐行长度，核对新增候选实际编码字节，并单列完整 decoded protocol 字节。S 实测新增 100/5,650 B，总 count105、全响应 7,296 B；nonce/时点等完整响应不得冒称固定 manifest 字节。

两种历史共享 `HISTORY_SHAPES`：同一合成 u/sc/mano，两独立会话，多小记录 10,000×64 汉字（正文 UTF8 总1,920,000 B）；大单记录1×1,048,576汉字（正文3,145,728 B）。重复“合成文”截断，固定日期、user role、其余正文 NULL。SQL 实际 count/length/blob length 保存在 `seeded-sql.json`。S 公共响应分别 3,198,927/3,145,858 B，完整原 Python oracle 相等。

## 实际结果与支持包络

并发1/4/8只针对**完整面板**；两类历史各单并发；混合格为面板4并发+两条持续合成模型SSE+普通HTTP。不声称所有 API 或历史同时8并发。各稳定格5批预热后20批测量；S 面板稳定完成20/80/160请求，历史各20完成，混合20轮完成80面板/20普通HTTP/40真实模型取消。预热不加入测量请求数。

| 证据实例 | 功能 | 面板1/4/8 p95 | 历史/双SSE/槽恢复 | 结论 |
|---|---|---|---|---|
| `loop-s-03` | 1 passed，2.70 s | 未跑容量 | 公开流首delta46 ms、取消约2 ms；另有真实 offline 目录证据 | loopback 功能基线 |
| `bridge-s-03` | 1 passed，2.52 s | 57.9 / 64.0 / 98.4 ms | 两历史均完成；双SSE取消最大观察13.0 ms；8→拒绝9→释放→再占8；最终 tasks8，队列/读者/模型0 | S 本地指定稳定场景通过 |
| `bridge-m-01` | 1 passed，2.87 s | 280.8 / 1097.3 / 2024.8 ms | 两历史完成；双SSE取消最大观察15.62ms；槽恢复与最终tasks8/队列读者模型0；容量innerexit0 | M 本地指定稳定场景通过；不是生产SLO |
| `bridge-l-01` | 1 passed，5.31s | c1 p95=2141.3ms；c4中断；c8未跑 | Wire新增100000/5650000B、完整decoded5651649B；新阶段门审查时主动SIGINT130，全部OOM观察0 | 审查安全中断，不能称L通过/失败或OOM；不并入后续新L结果 |
| `bridge-l-02` | 1 passed，5.36 s；功能及阶段出口均0 | 2220.3 / 10142.5 ms / 首预热8请求均500 | 后续历史、mixed、slots因OOM安全未执行 | L1/4局部完成；L8不支持，非完整L通过 |

8 槽定向格实际私有503、当前公共502，独立计容量拒绝；不是健康200。稳定格无预期过载请求。普通502保留为 infrastructure 待结合上游/cgroup 分类；client取消、业务响应差分、OS OOM、V8工具堆OOM分别记录。任一档真实OOM/持续积压后停止该档更高格，未执行记“安全停止”，不记skip/pass，不提高预算。

L02从最终固定样本启动：实际count100005、新增记录5,650,000 B、完整decoded私有响应5,651,649 B。c1完成5预热+20稳定，最大2.2613s；c4完成5预热+20稳定（80请求），最大10.2350s。c8首个预热批约13.54s后8个公开响应均500。实际Docker事件在1793372232.123201秒记录Go OOM；Go exited137/OOMKilled=true，其余角色当时仍running/OOMKilled=false。Next日志`UND_ERR_SOCKET`对应上游Go连接关闭，因此是固定Go96MiB预算的基础设施边界，**不是8槽过载503→502，也不据此修改业务错误语义**。

原driver先见到响应差分并记录`request-or-harness-error`/exit1；异步OOM证据随后到达，外层实时门保留oom-stop并退出86。原日志不改写；独立`bridge-l-02/failure-classification.json`关联实际事件、State及响应，最终分类为budget-oom。该档剩余预热、20批8并发稳定、两历史负载、mixed及slots均未继续，没有重试或扩限。

L01是另一轮审查安全中断：c1完成，c4被针对driver PID的SIGINT中断，c8未跑；四角色OOM记录均0。它不作为L成功或OOM失败，也不与L02数量累加。

## 采样口径和资源曲线

`scripts/stage-sample.sh` 在各独占 cgroup 内运行 shell/awk/stat，目标间隔200ms，实际 timestamp/间隔保留；S 每角色156点，所有角色最大间隔约251ms。观察开销计入各角色限额。`stage_summary.py` 输出 `resource-summary.json`，按每格测量起止 UTC timestamp 划分资源曲线；CPU 为 cgroup usage_usec 差值/实际时间（一个CPU=100%），不是 Go heap。

memory.current、进程RSS、未链接 fd 分配块均只有**采样峰值**。memory.peak 是未重置的容器生命周期高水位，包含启动和功能基线，不能当每格独立峰值；单独展示。sumRSS 会重复计算共享页，PID1 RSS另列；load PID1为sleep，因此总进程RSS更有意义。各容器 memory.events 与 Docker State.OOMKilled 独立观察，若进程退出导致末次cgroup不可读会明确记不可读，不从旧容器exit137推断OOM。没有 PSI 数据。

临时 spool 可已 unlink，普通du会漏掉；采样通过 `/proc/*/fd` 对 `(deleted)` 的 device/inode 去重，再统计 stat blocks×512。S Go 观察到分配块峰值6,742,016 B；这个值不是瞬时最高或逻辑明文总量。文件系统 df 与容器层大小收据另存。正常结束后文件描述符关闭/资源回落由实际样本与队列后态判断，不借外部清理制造通过。

| S角色 | 采样 memory.current max B | 观测到的生命周期 memory.peak B | CPU区间均值（单核100%） |
|---|---:|---:|---:|
| Go | 28,655,616 | 71,847,936 | 28.1% |
| Python | 220,049,408 | 225,349,632 | 70.4% |
| Next | 76,730,368 | 77,848,576 | 8.6% |
| load | 221,315,072 | 277,807,104 | 10.5% |

M 的对应生命周期高水位为 Go66,433,024 / Python242,475,008 / Next92,672,000 / load279,592,960 B；采样memory.current最大分别54,939,648 / 242,372,608 / 92,082,176 / 219,619,328 B。M容量约132秒，CPU均值Go28.7%/Python97.3%/Next6.8%/load7.3%，Go已unlink fd分配块采样峰值6,266,880 B。四角色swap采样0、memory.events oom/kill0、该区间Docker OOM事件为空。按格曲线及最大实际采样间隔见M的`resource-summary.json`。

上述区间为S整个容量入口，生命周期高水位包含此前功能基线；后续单独协议/静态回归没有混入容量采样。

L02采样区间约316.4s，最大实际间隔各角色约234/237/238/331ms。Go最后采样memory.current=97,726,464 B、观测到的生命周期peak=97,820,672 B，距真实OOM约148ms；此时采样OOM计数仍0。PID1被杀后cgroup不再可读，不能把末次0说成没有OOM，也不能把100,663,296 B限额当测得的终端峰值。实际OOM由live Docker事件及State独立证明。

L02 Python sampled current最大624,365,568 B、生命周期观测peak624,975,872 B；Next为106,745,856/143,302,656 B；load为264,744,960/429,985,792 B。这三角未OOM，swap采样0。L1/4完成测量格内Go sampled current最大分别43,917,312/74,141,696 B，Python分别241,291,264/405,360,640 B。完整曲线由逐时点原日志、各run的`resource-summary.json`及根收尾CSV `root-logs/resource-curves-20261031.csv`提供；CSV有56条按格/角色汇总，生命周期peak与采样值列分开。L8没有完整稳定格，不人为补曲线。

## 故障、升级和回归

受影响选择由 `sh scripts/affected-tests.sh --list 36f105cf...` 保存于 `logs/affected-selection.txt`；没有前端源变更，不重复 Next build。Go业务源码未变，不机械重跑旧全部unit/race/vet。全部语言执行在专属Docker内。

| 当前本票检查 | 结果/证据 |
|---|---|
| 固定数据contract | 首次missing-module红日志01；最终literal字节/分布/历史断言绿，包含在正式回归 |
| loopback/bridge全native公开API+SQL | 各独立实例功能日志；最终实例见上表，不累加历史候选 |
| private Wire+manifest正式回归 | `bridge-s-03/protocol-regression.log`：12 passed，78.74 s，innerexit0；真实10/60/1秒，writer入队取消、乱序、EOF/无EOF、8槽、身份变更等 |
| Go真实公开/版本/进程loss定向回归 | `bridge-s-03/go-boundaries.log`：4 passed/7 deselected，2.96 s；原版本Go、新Go flagoff、真实旧Python不匹配502、原请求无重放 |
| Ruff | 正式文件检查通过，日志保留 |
| Pyright | 保持load512MiB硬预算、384MiB工具堆，对本票8受影响文件检查0 errors；完整项目不是本轮pass |
| bridge定点故障 | `bridge-fault-s-02/faults.jsonl`，innerexit0；实际private/public401/428/403完整body、严格end/EOF、无EOF1.037s退出/恢复8槽，真实公开取消2.07ms传至owner；释放gate前实际入队且余额0，之后原事务创建draw1，实际private delta1 |
| 独立两轴 | `review-final.md`：Standards P3历史shape重复已共享；Spec P2 bridge故障证据缺口由fault-s02补齐，两轴定点复核闭合，无生产P1/P2 |
| 最终窄测/静态（10月31日） | 新独占512MiB/no-network夹具，`root-logs/final-unit-20261031.log`为4 passed/2 warnings/0.61s；8个受影响Python文件Ruff通过、Pyright0 errors/0 warnings；两shell文件Docker内`sh -n`通过。准确命令另见根收尾收据。 |

表格是各命令的实际结果，存在重复的数据contract或功能场景，**不合并成一个虚增测试总数**。本票未运行Python全库、前端单测/类型检查/重新构建或Go全量编译检查；没有相应业务源码变更，使用已固定的生产Next包和Go二进制验实际链路。选择性Go边界是Python驱动真实二进制，不冒称本轮重新执行Go unit/race/vet/build。

上述protocol/版本选择在load cgroup内额外loopback TCP服务上执行，不是独立Go96MiB角色的容量测量；bridge故障、固定角色预算与S/M/L负载各有独立实例证据，不能混写。

新增gate窄测为`gate-operator-green.log`的4 passed：固定数据literal、功能exit0+OOM拒绝、实际capacity.run在读取endpoints/发送HTTP前拒绝、operator独立分类。真实64MiB/no-network `oom-child-probe`内256MiB子分配被内核kill(-9)，memory.events oom1/kill1、peak67,108,864 B，PID1仍活；Docker实际State为Running=true且OOMKilled=true。host live事件确已写marker，正式shell入口在无socket的Docker helper中退出86。probe里的`functional.exit=0`是人为设置的**成功功能反例前态**，不计业务功能通过；真实证明是子OOM、父进程存活与准入拒绝。

`signal-cleanup-probe`为另一32MiB健康容器：实际TTY Ctrl-C后控制进程exit130，operator=INT、sampler sentinel存在、没有oom-stop，日志最终49行且容器内仅剩sleep。证明采样/watcher已收束，未跑全矩阵来验证这项清理。两项probe均已stop保留，不能计入S/M/L的业务OOM/完成计数。最终两轴对live gate和公共cleanup也已定点闭合（完整历史见review-final.md）。

10月31日补生命周期准入：functional/faults/capacity先检查本票角色State，历史OOMKilled=true即86；inspect失败也失败关闭但不冒称OOM。独立新`root-lifecycle-20261031-01`在无live watcher、无已有停止marker的情况下先发生真实child OOM，PID1仍Running且State.OOMKilled=true，正式`check-probe-state`仍返回86。其functional.exit0也是人工构造的成功前态，不是额外业务测试pass。取watcher开始时间已移到State检查之前，避免两种观察之间跨秒缺口；仅此两行位置硬化通过只读复核/语法检查，不重跑大矩阵。

真实60秒测试会在30秒触发仓库 faulthandler 栈诊断，这是等待证据，不是失败；故意不遵约peer也可产生Uvicorn短Content-Length日志，不能从测试输出中删除。最终以独立测试断言与innerexit为准，同时保留日志供复核。

403故障使用已建立的真实合成OAuth session，再由仅测试夹具的原`database.write`设置banned_at并保留session，模拟现有in-flight身份前态；正常admin ban会撤销session，不能把该夹具说成其公开发封禁操作后的常态。writer场景的用户则在基线R2全用户发卡之后才真实OAuth注册/同意条款，自然无余额。文件门只启用于显式`STAGE_FAULTS=1`，不新增业务HTTP接口；放开writer gate只用于验证已授权原事务提交，**取消成功在放开之前已观测**，不把救援当成功。

## 首次失败与操作偏差

所有旧日志、数据与容器均保留，没有重写失败成通过。

1. 首prepare和`loop-s-01`误将backend目录整体RO挂载。随后仅文件名预检发现backend/.env存在，立即receipt/stop并作废；未读取、复制、打印该文件内容，未运行该组功能/容量，Settings显式合成且网络none。此路径暴露本身仍记录为偏差，不夸大为完整系统安全审计。后续仅src/tests/pyproject等精确白名单，扫描敏感文件名/输入符号链接，并对实际Mounts再核白名单。
2. 停组时正在读取的shell脚本被编辑，出现尾部`inspect: command not found`；实际4角色已停止。自动审批拒绝直接起新组，随后只读ps/inspect证明全停后才继续。没有绕过审批，也没有删除旧组。
3. `loop-s-02`功能通过，但末行沿用“Python stop后目录存活”旧文案；该日志不证明外部Python已停。当前源码修正文案，`loop-s-03/offline.json`是真实stop后证据。
4. `bridge-s-01`原test Cookie绑定Next DNS，directPython401导致功能失败；当时流程仍提前启动capacity属于流程错误。其已完成子格不计最终S pass。现复制仅此合成Cookie供跨DNSoracle，并强制functional.exit0门禁。
5. 同组SSE临时iterator被finalize，model-events显示start后毫秒级client取消，故activeModels==2不稳定。保留(response, iterator)直到显式关闭后，S03完整混合20轮通过。没有删activeModels断言使其变绿。
6. `bridge-s-02`把内部`http://next:3000`当浏览器Origin，创建403；现在使用原配置允许的合成localhost站点，保留攻击Origin拒绝断言，不改认证规则。门禁正确阻止该组capacity。
7. 首完整Pyright因uv镜像缺Node尝试nodeenv联网，被internal网络拒绝，未下载/安装。精确复用既有Node后，完整项目又触发V8默认约256MiB工具堆OOM（非cgroup OOM），保留日志。最终只检查受影响文件，384MiB工具堆仍在原512MiB硬限额内，不能称完整项目检查通过。
8. `bridge-fault-s-01`晚期预期空余额不成立：完整基线的R2全用户发卡已为初始fault用户合法建余额。没有清空账本迎合测试；改用基线后才真实OAuth注册的新writer用户，自然获得无余额前态，独立新组重跑。
9. 初版仅在阶段后保存OOM，可能在child/observer OOM但HTTP仍成功时继续高格；独立审查判为harness P2。L01 c1完成后立即SIGINT中断c4，四角色全部OOM事件/计数0。新增实时event→marker→批次准入门和实际child-OOM反例后才以新L实例继续；S/M旧结果OOM0有效，但不追认为经新实时门运行。
10. 初版signal trap只关闭watcher，可能留下sampler。公共cleanup已统一sentinel/wait/watch顺序，operator单列，真实INT证据见上文；不删除旧日志掩盖修订。
11. 实时watcher原仅覆盖阶段期间，启动/阶段间的存活型OOM可能漏入下一阶段。补四角色生命周期准入，并将watcher开始取时置于检查前使覆盖重叠；独立新反例证明无live marker也拒绝。该harness缺口已复核关闭，不追认旧S/M/L在执行时已有最后这层防护；原记录OOM0的S/M数据仍有效。

## 源与测量修订

M与L01保存`source/backend/tests/`八个正式Python文件、`source/scripts/`入口/采样器和`harness-source-sha256.txt`，与各自release摘要分开。早期S03完整fixture快照当时未保存，不能把最后宿主fixture SHA追认为其已加载版本。S03之后的改变包括：相同数据的HISTORY_SHAPES共享、默认关闭的faults门、新增M暖场前的实际Wire字节probe、model-event只记synthetic marker、L独立功能采样、实时OOM门与signal cleanup；没有业务/协议/预算改变。M/L01实际业务负载/资源数据保留，其旧门禁能力与新门禁验证不混淆。后续L源快照在执行前固定；若仪器only修订不影响旧场景，会明确记录而不机械重跑所有档。

每轮为新进程和合成SQLite；OS文件页缓存可能已被既有只读产物/前档预热，没有清理系统缓存。5轮warmup后的延迟是热态稳定样本，不是冷磁盘启动SLO。两条SSE是先输出真实delta后保持模型挂起、并在面板负载期间仍active的本地合成流，未测持续高吞吐音频或外部真实模型。

最终生产代码仍为基点36f105cf，没有修改Go/Python业务实现。受测Go SHA为`29a2d409091cd757cc419fb4f655f3d507371c49da3d332bc4f83015a9b0bdb5`；固定Next server为`3a44d862ae978d8ebe4195c385c5820e04ba413be37d6e5ebe75ffa4a3a7227c`、compiled API route为`0113206246f62f07f1c31eacf30883c5f55ac1c8152ececadf0883bd4d596eae`，与上一票冻结产物一致。旧Go、Node及最终10份测试/脚本源码SHA另见根清单；整个venv不宣称有单一重新构建hash。L02后的生命周期/时间衔接仅改测试入口，独立probe/窄测验证；没有回填旧run源码快照或重写旧结果。

## 保留资源和未证明范围

本票所有容器/网络名以前缀 `eidolon-stage-close-20261030-` 归属。最终只读盘点为**73个专属容器全部exited、8个内部bridge保留、无新增镜像/命名卷**；只复用uv与Node两个既有image ID。73包含准备、资源/安全门探针与失败实例，不是73个同时运行服务；测量始终仅4个受限角色。所有已存在的用户容器未启动、未承担本票压力。没有privileged或发布宿主端口；初次过宽挂载偏差已在上文明确披露，最终有效组为窄白名单挂载。

专属容器可写层SizeRw合计**152,698,880 B /145.625MiB**，主要增量包括曾精确复制的Node/旧Go、临时工具与日志；不是73份镜像体积，也不是宿主物理分配量。宿主独立artifact目录`/private/tmp/eidolon-stage-close-20261030-artifacts`采样为1,300,848KiB（约1270.36MiB）；项目scratch收尾前采样191,240KiB（约186.76MiB，最后收据会略增长）。两类是bind源，未计入容器层；不将其与OrbStack虚拟盘物理分配混为一谈。源文件/正式文档亦为宿主额外文件；没有在宿主安装或执行语言构建/测试。

最终原始inspect、网络清单、层汇总和artifact SHA位于`root-logs/`，最终文件范围/收据另存。10月31日根只汇总已保存的L数据并停止当时仍闲置的3个L角色，没有再跑大负载；辅助probe/检查容器亦停止。初次错误组、所有合成数据库/备份、独立basetemp、FIFO、失败日志和缓存全部保留，不remove、不prune。以后若维护者选择清理，应先归档/核对本票资源清单，只针对确认归属的测试资源与精确artifact目录，不删除共享旧镜像或用户其他文件；本轮未执行清理。

本地合成规模不是生产用户数、真实模型时延或SLO。此报告不证明经济作品隔离、第二作品运行时、主要后端已完整变成Go、持久创建意图收据、生产部署/回滚或外部代理可用。局部flagoff/旧版本回归不删除可能已提交的行，不把重发当回滚。最终只可据明确场景、实际预算和档位给支持结论。

本次可收尾的是**这一波已迁SC原生能力的本地集成实验与边界报告**：指定S/M稳定场景有证据，L8明确不在Go96MiB预算支持范围，未执行格保留。它不等于所有测试均绿或全拓扑容量合格。未发现需改业务源码的P1/P2；发现的harness覆盖、阶段门及清理问题均有最小修复和独立复核。当前只交待审diff，不自动提交或进入下一业务迁移。

## 2026-10-31协调接受与本地提交收尾

上述“待审/未提交”描述保留为实验完成时状态。协调此后逐项核对最终11文件SHA和diff --check，另行Standards/Spec只读审查各0 finding，接受S/M指定场景、L1/4局部完成和L8真实OOM的边界结论；没有重跑测试，也没有要求扩限使L变绿。

现获授权仅本地提交final-files.txt列明11个正式文件；scratch、日志、凭据、DB、缓存和私人资料全部不入提交、不清理，73专属容器继续停止保留。实际提交SHA及精确清单另记`.scratch/go-migration-stage-close/local-commit-receipt.md`，避免自引用提交SHA。无push/PR/部署或生产许可。后续只准备第二作品最小文本闭环规格，不在本次收尾实施新的业务、Schema或非SC经济。
