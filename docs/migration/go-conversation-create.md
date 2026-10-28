# Go SC 会话创建命令切片验收

日期：2026-10-28。基线 `331978d1aef32e0ba08346d10931039482aef670`，分支 `refactor/multi-work-dlc`。规格和协调审查补充保存在 `.scratch/go-conversation-create/spec.md` 第9节，单票 `issues/01-go-create.md`。本地合成验收；不代表生产启用、第三阶段全部完成或持久幂等已实现。

## 已实现的边界

- Go `conversationcreate.NewHandler` 真正执行匹配启动内容摘要的 SC 角色成员准入，生成一次 UUIDv4 hex，以固定版本/用户/SC/角色/摘要构造单次创建命令。创建开关只接管 `POST /api/conversations`；未配置保留旧代理。
- Python `/api/_core/sc-conversation-create/prepare` 仅提供认证输入；`execute` 重新认证原 Cookie、Origin、用户、SC、角色和摘要，经原 Store / Database 单写事务插入一条会话。时间仍由 Python `_now` 在入队前生成，title 为 NULL，原当前会话的时间/rowid选择不变。无消息、事件、奖励、记忆、标题任务等额外初始化。
- 新增独立 owner-only 创建凭据配置；与只读凭据同值拒绝启用。未配置接口关闭，读凭据加合法用户 Cookie 也不能写。Go 外部私有命名空间继续全部封锁。摘要来自启动已加载 v3 元数据，不按请求读取文件，不信任客户端登记。
- Go 保留 prepare/execute 的每条 Set-Cookie 原顺序，不使用共享 CookieJar；准备后角色404、摘要错配、执行失败/未知仍保留已取得的续期 Cookie。
- 直连 Python 创建补齐 body/header/query 的显式 SC 检查，冲突重复 JSON 作品声明不能以最后值覆盖隐藏。其它未知公开字段仍忽略。不改上传、流式回合、OAuth、WebRTC 协议。Go 旧创建 1MiB 限制保留，不把该上限加入 Python 直连或其它接口。
- 原公开 201 五字段不变。非法作品409、原认证401/403/428、创建模型422及非法UTF-8的固定公开解析400保持；Go 自己的未知角色404。准备/合同失败502；execute已可能接收后的通信、合同或内部拒绝返回502未知结果提示，不透传私有错误或行信息。

## 单次发送不等于持久幂等

Schema仍为v3，无收据/墓碑/表升级，不复用R2奖励账本。相同body两次公共POST仍是两个意图、两个ID。私有ID冲突拒绝，不REPLACE、不重生成、不返回缓存结果；行删除后无法证明旧命令已执行，因此私有重放不受支持且协议禁止。

写客户端独立禁复用HTTP/1、无GetBody/重定向/自动重试/客户端幂等头/旧接口回退。prepare后发送前显式检查公共context；此前取消零执行。入队后取消不撤回（ADR-0028）；INSERT失败原事务回滚；COMMIT后丢失响应可能已创建。Go不把异常解释为未提交，也不自动补发。通用未知提示要求先核对已有会话列表。旧客户端再次手动POST仍是新意图，这是已批准的兼容限制。

SQLite同步级别未变，201不是电源故障零丢失保证。HTTP5秒超时只限制等待，不终止Python已入队事务；不能据超时判断数据库结果。回滚只恢复后续旧路由，不删除已提交或可能提交的行。需要跨重启/删除安全恢复时另审客户端意图键及Python持久收据/删除政策。

## 首次失败与逐步证据

日志目录 `.scratch/go-conversation-create/`，均保留：

| 日志 | 实际结果与解释 |
|---|---|
| scope-red.log | 4 failed、1 passed；Demo/null/requestData与冲突重复work_id原实现返回201，真实SC会话被创建。 |
| scope-green.log | 最小作用域补齐后5 passed。最早同名文件内曾是容器sh不支持花括号复制的命令错误，后续正确同步的成功结果见当前文件；业务首次失败在scope-red.log保留。 |
| command-red.log | 4 failed；专用接口尚未实现返回404，凭据同值未阻止启动。 |
| command-green.log | 1 failed、8 passed；测试误用不存在的历史路径得到405，改用实际/messages后通过。 |
| go-command-red.log | 编译失败：处理器尚不存在。 |
| go-command-green.log、go-expanded.log | 创建及多Cookie、准备后拒绝、真实TCP断连单次发送、11类非法已提交结果/重定向合同测试通过。 |
| http-first.log | 13 passed；基础HTTP/SQLite及取消/丢响应/回滚通过。故障用ASGI在真实201提交后主动抛错，日志中的预期异常不是生产错误。 |
| http-expanded.log | 2 failed、5 passed；旧三进程观测仍要求公开创建路径，实际已走私有创建，修正为逐阶段原Origin/SC头验证；SSE及实际业务本身通过。 |
| http-static.log | 8 passed；真实三进程含新创建，随后ruff发现测试未用import及分号（4项），未执行其后的pyright。 |
| static-second.log | ruff通过，pyright零错误/警告。 |
| original-priority.log | 原提交app副本探针1 passed，记录登录/缺字段/非法类型/未知角色/坏JSON优先级；不切换Git、不修改工作区。 |
| python-affected-first.log | pytest收集失败：误写不存在的奖励测试文件名，没有运行测试；更正后重跑，不能算通过。 |

最终统计、独立审查及资源见下方追加；历史失败不改成成功。

## 尚未实现及风险

无持久幂等/结果查询协议、无非SC创建、无Go SQLite写入、无送礼隐式创建迁移、无生产配置变更。未测试电源故障、磁盘耗尽、真实生产数据或容量峰值；原写队列仍可能排队较久，增加两次本地HTTP及重复认证。匹配快照、独立凭据分发、进程排空和生产上线均须另审。


## 最终复验与未解决阻塞（追加）

**结论：创建切片已实现并通过专项，整体验收未完成；按用户明确选择保留改动、不暂存、不提交。** 现有三进程代理拒绝响应截断另票 `.scratch/proxy-origin-rejection/issues/01-response-truncation.md`，本票没有改代理掩盖它。

| 最终/补充日志 | 实际结果 | 与最终代码的关系 |
|---|---|---|
| python-affected-second.log | 459 passed、2 failed | 初次大组：新账号删除测试裸SQL制造悬空session，已换真实Store.delete_user；另一个为Next三进程Origin拒绝响应截断。不是全通过。 |
| proxy-reject-probe-first.log、origin-old-binary-next.log | 最小探针1 failed | 旧`/work/eidolon-core-summaries`在第14轮origin拒绝复现RemoteProtocolError；Next failed to pipe response / UND_ERR_SOCKET。首次失败即停，未执行脚本后续两组，不能说三组都比较过。原大组进程临时文件随后被pytest默认保留策略回收，未能另外复制；大组pytest失败日志与旧版探针Next原日志已留存。 |
| review-results.md | 独立Standards 1×P3、Spec 2×P2 | 重复测试已归并；prepare 64KiB截断、422私有字段透传均先红后绿。两轴只读复核确认闭环，未假称代理截断也修复。 |
| review-contract-red.log、review-size-red.log | 各1失败 | 私有422字段可公开及70KiB合法未知角色由404变502均实测命中。 |
| review-go-green.log、review-http-static.log | Go专项通过；Python创建17 passed | P2修复后；当时测试app类型窄化导致pyright1错误，后修复。 |
| static-final.log、final-create-extra.log | 静态通过；11项实际Go创建测试通过 | 包含并发双意图两条会话、真实认证后删除账号导致FK拒绝、实际INSERT中断/回滚与已提交响应丢失。 |
| python-affected-final.log | **460 passed，0 failed，0 skipped**（39项弃用警告） | 按`affected-list.log`32文件清单去掉已单列阻塞的三进程文件，再补HTTP间接关联测试；Docker `-n auto`，工作进程上限2。本组在最后非法UTF-8小补丁前运行，非Python全量。未把排除的三进程计入通过。 |
| utf8-red.log | 1 failed、10 deselected | 独立复核建议的边缘实测：原非法UTF-8返回400，新路径502。 |
| go-utf8-final.log | **8个测试包全通过，TCP、race、vet、build通过** | 最终Go源码；仅prepare允许固定公开解析400，任何未知400/私有额外字段或execute400仍失败关闭。 |
| utf8-http-static-final.log | **19 passed，0 failed，0 skipped**；ruff通过、pyright **0 errors/0 warnings** | 最终源码+最终二进制全部创建专项，含UTF-8/大输入/旧错误优先级。与460项有重叠，不能相加成479项独立测试。 |
| source-match-final.log | 本地与容器src/tests/Go源码无差异，双容器最终二进制摘要相同 | 只排除__pycache__及既有容器测试探针test_state_reward_audit_probe.py；未把额外探针算入正式验收。 |

本轮早期`http-static.log`真实Next生产包→Go→FastAPI两种素材基址均通过：SSE首段46.277/49.039ms、断开到模型取消7.010/4.616ms，OAuth多Cookie、SC历史及记忆/R1/R2也有实际证据。随后大组出现代理截断，旧版最小复现确认仍存在，所以早期通过不能覆盖后续失败。最后400小补丁后未重新运行三进程，不把早期日志写成最终链路已验收。未运行Python全量或前端全量/新生产构建；前端源码未改，复用既有生产包，所用二进制和证据阶段明确区分。

### 可复验命令

Docker `/work/core`：`EIDOLON_TCP_TESTS=1 go test -count=1 ./...`，`EIDOLON_TCP_TESTS=1 go test -race -count=1 ./...`，`go vet ./...`，`go build -o /work/eidolon-core-create-utf8 ./cmd/eidolon-core`。

Docker `/work/backend`：设置EIDOLON_CORE_BINARY/EIDOLON_CREATE_BINARY为`/work/eidolon-core-create-utf8`，执行`.venv/bin/pytest -q -s tests/test_conversation_create.py tests/test_sc_create_command.py tests/test_go_conversation_create.py`；ruff与pyright命令见日志。受影响清单来自宿主机只读`sh scripts/affected-tests.sh --list 331978d1...`；测试/静态以容器已有.venv执行等价步骤（未在宿主机运行脚本的安装/测试分支）。原清单、实际参数和命令保留在scratch及工具记录。

### 资源与文件

复用且仅启动既有Python、Go两容器；未新增镜像、容器、卷、依赖或发布端口，Node服务在Python隔离容器内使用已存在二进制/生产包。未改宿主机系统配置或安装语言依赖。

| 可写层 | 开始 bytes | 期末 bytes | 净增 bytes |
|---|---:|---:|---:|
| eidolon-accept-python-20261024 | 2,696,708,096 | 2,953,625,600 | 256,917,504 |
| eidolon-accept-p2-go-20261024 | 667,684,864 | 712,876,032 | 45,191,168 |

合计 **302,108,672 bytes，约288.11MiB净增**，包括多版构建二进制、Go缓存、合成测试目录；不是毛新增总量或内存峰值。两容器未发布宿主机端口。最终二进制11,053,629 bytes，SHA-256 `cec13bc8cd165ed765ee7e960b0aa38607542a5e0ec9df98a87c8afb82042f4e`。

宿主机有本票源码/测试/正式文档、约2.25MiB创建scratch及单独阻塞票；此外为二进制传输留下`/tmp/eidolon-core-create-transfer`（11,050,787 bytes，早期版本）与空`/tmp/add-create-tests.txt`。没有宿主机构建或安装；这些额外临时文件也保留，未擅自清理。最终二进制后续通过容器标准输入流转发，无额外宿主机副本。

收尾仅停止本轮启动的两容器，保留所有资源。将来经审查可逐项移除容器`/work/eidolon-core-create*`、`/work/create-probes/`、本票新建`/work/create-affected-final-20261028`、`/work/create-utf8-final-20261028`及宿主机上述临时文件/本票scratch；不要删除复用容器整体、已有卷、用户文件或全局prune。未测RSS峰值、生产容量或真实用户迁移；没有生产数据许可。

### 待审修改范围与Git

本票21个必要文件，清单`.scratch/go-conversation-create/changed-files-final.txt`：Python新命令与作用域模块、app路由、启动摘要元数据、独立凭据加载/配置、Store受控ID插入；Go新命令/合同/故障测试、catalog内容元数据及路由、启动开关；三份创建测试及既有HTTP/三进程测试辅助；README、ADR0029和本报告。Schema、前端、生产配置未修改。

独立审查无本票剩余已确认P1/P2；已有代理响应截断仍为P2可用性阻塞，根因尚未确定。没有暂存/提交/推送/PR/部署，HEAD保持`331978d1aef32e0ba08346d10931039482aef670`；不提供新commit SHA。下一步只调查单列阻塞，不自动开始其它迁移。


## 2026-10-29 独立代理阻塞修复后的追加复验

上文2026-10-28“整体验收未完成/代理阻塞”保留为历史。用户随后授权独立处理阻塞，详见[代理修复验收](proxy-origin-rejection.md)。根因确认在既有Go HTTP/1服务器刷新早期响应时自动关闭上传body，与Transport抢读；非创建命令引入。生产修复仅在legacy proxy前启用FullDuplex，不扩大创建写能力、Schema、重试或作品范围。

最终生产二进制`d4573ba8defb689e306b1872cff5adeec4e2da0945427b48b2ddf72f95b2bf94`下，完整三进程、创建19专项及正式拒绝共23项通过，静态全通过；整组相关回归**464 passed/0 failed/0 skipped**，此轮包括三进程且在最终UTF-8补丁后。之后仅新增的Next3MiB双向上传/失败清理2项另跑通过，生产二进制未再变；Go全包/TCP/race/vet/build通过。不要与此前460或专项23相加为独立测试总数，不声称Python全量或前端全量/新构建。

三进程OAuth302/多Cookie、SC历史及记忆、创建prepare Cookie/执行前取消/提交后丢响应一次/未知不重发、R1/R2与SSE均通过；首段50.717/51.448ms、模型取消4.407/4.139ms。代理独立双轴发现的P2观测缺口和P3测试清理风险均补齐真实故障验证并只读复核关闭，未发现本票新增未解决P1/P2。有限本地合成测试不代表生产许可。

**当前状态：创建切片及其阻塞的必要本地验收已通过，等待维护者审查，不提交。** 累计待审24文件（新增代理TCP/Python回归及独立报告3项），清单在`.scratch/proxy-origin-rejection/combined-changed-files-final.txt`；未暂存、提交、推送、PR或部署，HEAD仍331978d1。没有自动推进其它功能。原版/修复版重复率、源码/生产包摘要、测试故障注入、当前轮资源增量（约357.85MiB）、清理方法及剩余范围见独立报告；上一轮约288.11MiB不改写成当前轮数字。

## 2026-10-29 协调复核接受与本地提交收尾

维护者经协调复核已接受创建代码、独立代理修复及本地验收，解除此前“待审不提交”的临时限制，授权仅本地Git收尾。采用一个组合提交：两票共享路由、测试与验收文档，创建的最终链路证据依赖代理修复；提交当前已验收的24文件组合，不另造未经验证的拆分中间状态。本次只追加收尾文档，不修改已验收业务源码或重复全量测试。历史未提交、阻塞状态均保留为当时记录。

本地提交完成后的准确SHA与文件核对收据保存在`.scratch/proxy-origin-rejection/local-commit-receipt.md`并在最终交付中报告（提交不能嵌入自己的SHA）。只暂存已审24文件白名单；scratch、日志、数据库、密钥、构建产物、.DS_Store与用户资料不进入提交。无push、PR、生产或部署。后续仅准备删除会话规格，未经规格审查不实施下一票。
