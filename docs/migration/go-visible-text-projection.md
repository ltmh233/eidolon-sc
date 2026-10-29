# Go 完整字段展示文字投影验收

2026-10-29；基点 `3eb78c469c7be7f5cda8d1aac4f9e3914eb93f65`，分支 `refactor/multi-work-dlc`。本轮实现与验收，未暂存、提交、push、开 PR 或部署。数据库均为测试创建的合成临时 SQLite；无 schema/Store/前端/生产配置变化，无依赖安装。全部构建和依赖执行在原有 Docker 容器内。

## 实现合同

`core/internal/timeline/visible_text.go` 的一个 `visibleText` 供历史与摘要复用：一次处理完整字段，删除从最早开符到第一个允许闭符的块，未闭合时采用 Python 的 80 码点条件及 finish 行为，保留中间空白、丢弃首尾空白。空白谓词固定 Python 3.11 的 29 码点，包含 001C–001F；200B/FEFF 与组合字符保持原样。不按网络块、segment 或语言共享状态，不作归一化，不截断预览。使用 UTF-8 字符串游标和最多 81 个码点的条件扫描，不为全部历史分配 rune 副本；仍需最大字段/row、输出字符串及原索引内存，不能称为恒定内存或性能提升。

历史仅过滤 character/reach-out 的 ja/zh 与各 segment；character 顶层 null 保持 null，text 保持原样，user 的合法字符串字段全部 bypass。摘要只过滤选中候选的顶层字段，不扩大传输 segments。Python v2 以结构检查标记原 `TypeError`/`AttributeError`，保留候选占位与 `display_error`，选中才转原标准 500；未选中坏候选不影响其他预览。Store 加载结构错误的原时点不变；历史坏 segment 的旧私有流会断开、旧 Go 实际返回 502，新路径也保持 502，不套用摘要的 500。

新 Core 对两个原生读取固定构造 `X-Core-Display-Text: raw-fields-v1`；Python 缺 header 走原 v1 过滤，存在则必须恰好一个精确值。非法值、重复（即使同值）、逗号值在读取/seen/title settle 前私有 409；Go 将该协议拒绝映射通用 502，原 SC 业务 409 保持。成功同时要求精确 ack 与 v2 meta；历史仍要求原 comparison ack/keys。客户端 capability 不选择私有模式，公开响应不携带私有 ack。v2 整个 row/meta 的原 JSON 字节先验证 UTF-8/UTF-16 surrogate，再做 Go JSON 解码，包含 gift_name、标题与 meta user；字面 `\\ud800` 与合法 surrogate pair 被接受，孤立 surrogate/非法 UTF-8 拒绝。此检查只在 v2 私有入口。

原加密临时 spool、完整 end+EOF 才发布、单次读取及上游取消不变。旧公开/v1、模型、实时回复、通话/TTS/音频继续 Python 过滤器。原始 SQL ja/zh/segments 在完整字段差分中逐项前后相等。

## 本轮实际测试

日志均保留在 [本票 logs](../../.scratch/go-visible-text-projection/logs/)，下列组有重复用例，不能相加为不同测试总数；所有成功组均无 skip。

| 组 | 命令/结果与证据 |
| --- | --- |
| 首红 | `red-go-projection-real-20261029.log`：v2 完整字段投影报 invalid contract；`red-python-negotiation-20261029.log`：非法协商原为 200；`red-summary-negotiation-20261029.log`：旧 summary 在拒绝前触发 settle；`red-wire-unicode-20261029.log`：Go 默认替换非法 Unicode 而错误成功。原失败均保留。 |
| Go 全包 | `EIDOLON_TCP_TESTS=1 go test -count=1 ./...`、`EIDOLON_TCP_TESTS=1 go test -race -count=1 ./...`、`go vet ./...`、`go build -trimpath -o /work/eidolon-core-visible-text ./cmd/eidolon-core`，8 个有测试包各自通过；见 `go-final-all-tcp-race-vet-build-20261029.log`。包括真实 TCP、HTTP 取消/完整性以及 spool 实际 FD 密文、篡改与短写/nonce 错误锁存。 |
| 复审后 Go | 仅 wrapper/命名清理后再跑 timeline 包 `-race`（TCP 已启用）、vet 与 build，见 `go-review-fixes-final-20261029.log`。 |
| 受影响清单 | 宿主 `sh scripts/affected-tests.sh --list <基点>` 保存 `affected-list-20261029.log`；按其 9 文件在 Docker 显式配置 Core/timeline/summaries/version/固定 Next 路径执行 `pytest -n auto -q -p no:cacheprovider`：**29 passed**，见 `python-affected-final-20261029.log`。使用 4 个 worker；不是声称完整脚本已在容器运行。 |
| 最终 oracle/链路 | 复审修复后 `pytest -q -s tests/test_visible_text_projection.py tests/test_go_timeline.py tests/test_go_summaries.py tests/test_three_process.py`：**6 passed**，见 `python-review-fixes-chain-oracle-final-20261029.log`。 |
| SQL/ReachOut 补证 | 同一对象 oracle 增加所有原 SQL 字段前后快照；同一 summary setup 补选中坏 reach-out 标准 500/未选中正常分支：`pytest -q -s tests/test_visible_text_projection.py tests/test_go_summaries.py`：**3 passed**，见 `python-sql-and-reach-error-final-20261029.log`。 |
| 直接 Python/实时 | `pytest -q -n auto -p no:cacheprovider` 跑 delivery_cues、text_turn、call_turns、voice_call、tts_models、sessions、consent、affinity_snapshot、timeline_snapshot、summary_snapshot：**90 passed**，见 `python-direct-realtime-regressions-20261029.log`。含无/错服务凭据、有服务凭据但无用户的 v2 raw 请求拒绝且不改 seen。 |
| 静态 | `ruff check --no-cache /source/backend/src /source/backend/tests`；`pyright --pythonpath /work/backend/.venv/bin/python`：通过、0 errors/0 warnings，见 `static-last-final-20261029.log`。 |

独立 oracle 使用 Python 原 `CueFilter` 新对象的一次 `feed(完整字段)` 加 `finish()[0]`，同时比较完整公开对象。固定 seed `20261029`，2021 个输入（其中 2000 个随机组合），另加 3 个 null/空串/user 对象；Worked examples、79/80/81 码点、超长闭合、错配/嵌套/未知 tag、finish、中间/首尾空白、29 空白码点、实际 `200B FEFF 0065 0301` 与字面转义文本均覆盖。顶层与 segment 故意不一致、跨 segment 开/闭符、每语言独立；character.text 含括号与 user 的非空 ja/zh/segments 都保持。单个 character 完整字段 **4,000,015 字节**，本轮最终比较约 0.6 秒；既有 3.9MB 用户预览、长列表及 10000 事件回归也通过。样本不能证明全域等价。

在只请求 v2 的窗口故障注入 `Message/ReachOut.without_cues` 不可用，Go 历史/摘要仍与事先捕获的公开结果一致；直接公开/v1 仍依赖原实现。反向构建独立 `/work/eidolon-core-visible-text-injected`，故意返回错误 Go 展示文字：oracle 首例即失败（`A  B` 对 `INJECTED_VISIBLE_TEXT_FAILURE`），见 `injected-go-filter-oracle-20261029.log`。注入仅在容器副本，已自动恢复源码；错误二进制不是最终产物。

## 真实版本组合与副作用

版本测试使用同一合成测试构造：真实旧 Go 二进制、当前新 Go，基点 `app.py`/`conversation_summaries.py` 的独立 Python 包副本与当前 Python。没有用假 wire 替代旧 Python。每组含三次真实回合触发背景标题任务，门控的是 FakeBackgroundLLM；调用原 `titles.settle`，release 后直接确认标题已持久化。早期 `versions-*` 只有门控入口观察，最终证据以 `versions-real-title-*` 为准，每组 **1 passed**：

| Core / Python | 历史 / 列表 | 实际副作用 |
| --- | --- | --- |
| 旧 / 旧 | 200 / 200 | 捕获 seen；原 title 等待完成并持久化 |
| 旧 / 新 | 200 / 200 | v1 过滤合同兼容；同上 |
| 新 / 新 | 200 / 200 | ack+v2，Go 过滤；同上 |
| 新 / 旧 | **502 / 502** | **旧 Python 已把捕获 reach-out 标 seen，列表也实际等到标题完成并写入，才由 Go 拒绝响应** |
| 新 Core，native 关闭 | 200 / 200 | 原公开代理，伪造客户端 display header 不启用 raw |

所有组每次公开请求仅一次上游读取、无重试，不公开合成 raw cue 标记。新 Python 的非法协商则在 seen/settle 前拒绝；这与新 Core 遇旧 Python 的失败副作用是两个不同边界。四组合还实测历史 ja=null segment：直连 TypeError，原生 Core 502。发布顺序必须先 Python 再 Core；回滚 Core 可保留新 Python，反向回滚 Python 前需停用/回滚对应 native Core。

## 固定生产 Next 的真实三进程

使用保留的 `/work/r2-next-standalone/server.js`，不重新构建前端；两个 assets_url 参数各运行真实 Next→新 Go→Uvicorn/FastAPI，临时合成 SQLite。合成模型输出包含 character cues，SQL 保留原字符与 segments；另保留合成 raw reach-out，公开历史/两摘要与直连旧 Python 一致且无 cue，用户括号保留。继续验证 scope/memory/create/delete/Origin/多 Cookie/SSE 首段与模型上游实际取消。

最终两例首段分别 **47.689 / 50.939ms**，实际 LLM 取消观察 **3.463 / 4.120ms**；这些是本地合成运行数据，不是服务 SLO。无真实模型调用、无新前端构建或全量前端验收声明。

## 资源、产物及限制

最终 6 测组以 10ms best-effort `/proc` 采样观察 8 个先后启动的 Go 进程、730 次样本：最大单进程/同刻合计 RSS 样本 **82,304KiB**；最大已 unlink Go FD 大小样本 **13,209,154 字节**；测试子进程 `ru_maxrss` **390,116KiB** 是测试执行子进程的高水位，不能算 Go 独立峰值。受影响并行组另记录 84,232KiB 单 Go、128,060KiB 合计样本。采样会漏过瞬时峰值，均非生产容量界限。原 FD 密文/篡改/短写/nonce 行为由 Go spool 测试断言。

Docker 可写层开始为 Go 755,867,648 / Python 3,450,949,632 字节。中途快照（resources-implementation-after）为 Go 899,915,776 / Python 3,517,952,000 字节；主协调收尾停止容器后的最终快照为 Go **899,915,776** / Python **3,436,441,600** 字节（resources-stopped-final）。最终分别变化 **+144,048,128 / -14,508,032 字节**，合计净增加 **129,540,096 字节，约123.54 MiB**。这些是可写层净变化，含编译缓存、测试临时目录生命周期、日志与保留的新/注入二进制，不是毛新增产物大小、宿主物理磁盘精确增量或单请求峰值。无新镜像/容器/卷或端口发布；两个本票启动的原容器已停止且内容保留，其他容器未操作，不删除资源。

源码一致性见 `source-match-last-final-20261029.log`（宿主与实际 `/work` 副本逐字/哈希相等）；产物见 `artifacts-after-final-20261029.log`：

- 当前 Go `/work/eidolon-core-visible-text` SHA256 `f0aaa7d9c2ea52488ffaa0b085e95f70f20de9c053a5fe95190e9da71e501e4a`。
- 原 Go `/work/eidolon-core-proxy-final` SHA256 仍为 `d4573ba8defb689e306b1872cff5adeec4e2da0945427b48b2ddf72f95b2bf94`。
- 固定 Next server SHA256 仍为 `3a44d862ae978d8ebe4195c385c5820e04ba413be37d6e5ebe75ffa4a3a7227c`。

有一次流程偏差：初次 Docker 二进制转移误写宿主新临时文件 `/private/tmp/eidolon-core-visible-text-transfer-20261029`（11,054,559 字节，SHA256 `872605aaf452f43d94b86cc8c680b3534f65121a04f6e14a6aeb792e236b9954`），未在宿主执行，已仅删除本轮新建的该文件；以后改为 `docker cp ... - | docker cp - ...`。清理事实保留于 `temporary-binary-cleanup-20261029.log`。

本轮保留的过程失败还包括：首次 Go mount/shell 命令未实际跑测（另有 real 红测）；大字符语料含 15 万重复 cue 时旧 Python oracle 本身在 `delivery_cues.feed` 超过 60 秒（`green-oracle-20261029.log`，1 failed/3 passed），改为相同完整大字段、首尾 cue，未放宽比较；Go 自身 15 万重复闭合 cue 的已知文字期望通过。一次该 Go 用例的期望少算一个中间空格，修正后通过。初次 ruff 命中容器既有、宿主不存在的 probe 未使用 import；随后源码检查先遇 RO cache，再遇 pyright 未指定虚拟环境的依赖解析错误，最终按上列显式命令通过。没有删除/修改既有探针，也不把预期故障注入和基线压力失败写成“全程无失败”。

## 独立审查与未跑项

[独立双轴复审](../../.scratch/go-visible-text-projection/review-final.md)：Standards 两项 P3（无调用 wrapper、开关命名）修复并复核关闭；Spec 无 P1/P2，完整对象与真实标题覆盖提醒已补并复核关闭。测试新增仅沿已批准接缝，未更改范围。

本轮未跑全量 Python、全量前端/新 Next build、生产并发或真实用户/模型负载；原 `oracle-probe.py/.log` 是规格准备的历史证据，不算本轮算法差分。没有生产启动、凭据/权限新增、数据迁移或部署动作。后续是否提交/发布由用户另行授权。

TDD 时序说明：最先保存的红测来自完整对象 worked example/v2 协商/Unicode wire，之后完成对应小竖切片；批量 Python 独立 oracle 的首次运行晚于算法实现。后续故障二进制证明 oracle 会真实失败，但不能据此声称“首次批量 Python oracle 红测先于实现”。这是测试顺序的流程偏差，保留事实与实际覆盖，不影响已观察到的最终差分结果。

## 最终待审清单与收尾

共20个交付文件（15处已跟踪修改、5个正式新文件），精确清单在`.scratch/go-visible-text-projection/final-manifest.md`；18份运行时源码/正式测试与受测副本逐字一致，另两份为README与本报告。宿主保留本票scratch约480KiB及这些源码/文档改动；没有宿主依赖安装或构建执行，唯一临时传输二进制的偏差与清理见上文。后续如需清理，先核对并导出日志，再只针对本票`/work/eidolon-core-visible-text*`产物、`/work/visible-text-legacy`旧包与日志记载的本票测试目录逐项处理；未执行任何清理命令，更不能使用全局prune或删除整个既有容器。

本轮HEAD仍为`3eb78c469c7be7f5cda8d1aac4f9e3914eb93f65`，暂存区为空，`git diff --check`通过，全部实现留作待审diff。未push、PR、部署或开启其他切片。已实现并有上述本地合成证据；尚未验收生产容量/真实数据/发布组合运维，不能作为生产启用许可。

## 2026-10-29 协调接受与本地提交收尾

协调已读完整生产diff、新模块、oracle/版本测试和本报告，核对关键日志及18份源码/测试SHA与当前文件一致；另一次独立Standards/Spec复核无新增finding。接受2021文本差分、真实版本副作用、Go检查及Python分组证据；协调未重跑测试，不累加29/6/90/3，也保留上述TDD时序和基线压力失败事实。

现授权仅将final-manifest.md中的20个正式文件作为一个完整本地提交，不拆出只暴露raw的中间状态。此前“待审/未提交”是当时状态；最终SHA与核对收据另记`.scratch/go-visible-text-projection/local-commit-receipt.md`，避免提交自引用。scratch、日志、数据库、产物和私人文件不入提交。没有push、PR、部署或资源清理。提交后仅盘点下一Go读模型并准备一项规格/单票，不实现下一票。
