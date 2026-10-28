# HTTP/1 早期拒绝响应截断：独立修复验收

2026-10-29（Asia/Shanghai；日志保留容器原时间），本地 `refactor/multi-work-dlc`，HEAD `331978d1aef32e0ba08346d10931039482aef670`。保留Go会话创建的全部未提交改动；本票只修复此前单列的代理阻塞，不将其称为创建命令引入的问题。用户经协调审查授权继续最小修复，仍禁止暂存、提交、推送、PR和部署。

## 根因与最小改动

`core/internal/catalog/http.go` 的既有 ReverseProxy 使用 `FlushInterval=-1`，会在上传仍进行时立即刷新后端响应。当前Go 1.27的HTTP/1服务器在默认非duplex模式下，会在刷新响应头前关闭/消费尚未读完的原请求体（`net/http/server.go` 的`!w.fullDuplex`路径）。代理Transport也正在读取同一个请求体，两者争用。后端在Origin中间件中未读body便返回403时，这个窗口最明显；正文模型校验先读完body的409没有触发同一竞态。

服务器的自动关闭使Transport读到 `http.ErrBodyReadAfterClose`，上传失败又关闭后端连接，正在复制的拒绝响应变为 `use of closed network connection` / unexpected EOF。Next只是收到不完整上游响应而报 UND_ERR_SOCKET，不是必要触发层：Go直连也能复现，旧二进制也存在。

最小TCP测试通过在两次真实网络body读取之间暂停，固定上述合法调度窗口：后端先刷新403头，服务器刷新时关闭上传body，恢复上传读端后立即出现ErrBodyReadAfterClose，客户端收到403头但正文0字节/EOF。仅用一次全新连接即可触发，连接复用不是必要条件。该测试不靠扩大超时或概率碰撞。

修复只在原legacy proxy调用前启用 `http.NewResponseController(w).EnableFullDuplex()`，让HTTP/1服务器不要在响应刷新时替代理读取器关闭body。HTTP/2本身可双向读写，非网络Recorder可能不实现控制器；实际独立Go服务器及支持Unwrap的测试包装器验证过。未新增排空、全量缓冲、自动重试、忽略中断、定时关闭、超时扩大、Schema、权限或Python业务改动。原有上下文取消和流式转发继续使用原ReverseProxy。

新增测试：
- Go `proxy_early_response_test.go`：固定竞态、未完成4MiB声明与无终止chunked上传的完整拒绝/断开释放、3MiB合法双向流式上传及SSE正常完成。首段收到前大块请求尚未发送，防止把全量缓冲当流式通过。
- Python `test_proxy_rejections.py`：实际Go/Next生产包，固定长度及chunked各100轮×3类403/409，共每跳600请求；比较合成SQLite已初始化SC资源及待每日补发状态，业务零写入。网络异常累计后仍断言失败，不重试原请求；finally中也检查业务快照。

## 红绿与分层证据

原始日志在 `.scratch/proxy-origin-rejection/logs-20261029/`；此前创建票失败日志保留。

| 证据 | 实际结果 |
|---|---|
| layers-first.log | 探针本身失败：把直连Python旧文字回合的header语义误当Go拒绝语义，且将全组塞一测试触发60秒限制。不能把其AssertionError算传输截断。修正为每组独立有界测试、只比较共享的body/Origin拒绝。 |
| layers-second.log | 8组均实际执行，6 failed/2 passed；下表有分组统计。所有失败均Origin提前拒绝的RemoteProtocolError，先读body的拒绝为0。 |
| early-response-red.log | 确定性TCP红测0.51秒失败：ErrBodyReadAfterClose、403头后0字节正文、unexpected EOF。 |
| early-response-green.log | 同一测试修复后连续20轮通过。 |
| streaming-regression-first.log | race下当时的TCP取消、固定竞态、未完上传及3MiB双向流式上传通过；其中未完上传只观察handler结束的证据后来被Spec指出不足，不能据此声称异步Read已停止，已补后述独立观测与故障注入。 |
| layers-fixed.log | 同样8组各400请求执行。6个目标/参照组零截断；旧2组是负对照测量，仍有失败，不能将pytest的8 passed解读成旧版本也已修复。 |
| production-regression-red.log | 正式Next生产链路测试在未修复UTF8最终二进制上失败：600请求中8次Origin截断（length/chunked各4次）。 |
| production-regression-green.log | 修复后2项正式回归通过，Go/Next各600次（1200合计）零截断、真实SC业务快照不变。 |
| go-final.log | 最终Go全包/TCP、race、vet、build通过。 |

每组400次 = body拒绝/Origin拒绝 × length/chunked × 各100。失败数除400是全组失败率；除200是Origin路径失败率，不混用分母。

| 实际组 | 修复前失败/400 | 修复后实验失败/400 |
|---|---:|---:|
| Python直接 | 0 | 0 |
| Next→Python | 0 | 0 |
| 旧Go直接（负对照） | 1 | 2 |
| Next→旧Go（负对照） | 29 | 12 |
| 新Go代理模式直接 | 2 | 0 |
| Next→新Go代理模式 | 35 | 0 |
| 新Go原生创建模式直接 | 2 | 0 |
| Next→新Go原生创建模式 | 27 | 0 |

旧控制组故意仍使用原二进制，实验间失败数变化是调度差异，不是把旧版改好了。修复目标四组累计1600请求0失败，加Python两组800参照；确定性红绿比一次概率绿更有说服力。有限重复不是数学上证明所有环境均无竞态。

本轮前端源码未改变，使用既有 `/work/r2-next-standalone/server.js` 生产包；其代理入口route.js SHA-256为`0113206246f62f07f1c31eacf30883c5f55ac1c8152ececadf0883bd4d596eae`。未把旧包冒称本轮新构建。尝试与Python容器/work/frontend/src比对未成功（该目录无src），日志frontend-source-check.log明确保留；运行的是固定摘要的实际生产包，当前Git前端无差异。本轮不声称重新构建或全前端测试通过。

## 最终链路、独立复审及资源

**结论：本票阻塞已修复，必要本地验收通过，停在待审工作区；不暂存、不提交。** 创建命令上一轮阻塞可在本地关闭，但不等于整阶段或生产验收许可。

| 最终证据 | 真实结果/边界 |
|---|---|
| final-chain-static.log | **23 passed**：完整三进程2场景、创建19专项、拒绝2场景；ruff通过、pyright零错误/警告。创建prepare续期Cookie、执行前取消、提交后丢响应仅一次、未知结果、不重发均随最终二进制复验。 |
| affected-final.log | **464 passed、0 failed、0 skipped**，39项既有弃用警告，60.77秒；最终UTF-8和FullDuplex补丁后的相关清单（含此前阻塞的三进程），并补HTTP间接关联。并非Python全量。 |
| upload-quiescence-first.log | 观测接入首轮编译失败（未使用变量），不是通过证据；修正后保留日志。 |
| upload-quiescence-second.log | race下length/chunked两场景通过：第二次真实Read进入后断开，必须Close已返回且active Read全退出，再检查handler结束。 |
| upload-quiescence-fault-first.log | 注入副本路径错误，正常测试通过；**未命中故障，不计验证通过**。 |
| upload-quiescence-fault-second.log | cmp/grep先确认真正注入；实际Read在EOF后仍挂起，退出断言在**2.06秒正确失败**，随后独立defer放行并正常清理，没有等到总测试超时。证明清理不会误判成功。正式源码由trap恢复。 |
| next-upload-first.log、next-upload-static-final.log | 实际Next→Go→ASGI 3MiB上传，首段在bulk/请求终止块发送前已到达；正常收到3145729字节及完成帧。取消只由真实http.disconnect置位，stopping不算成功。 |
| upload-cleanup-final.log | 后续新增两项 **2 passed**（原正式文件其余2项deselected，并非跳过失败）：Next首段**4.128ms**、3145729字节、上游取消**2.247ms**；真实不读取的TCP对端让14166421字节发送缓冲保持阻塞，普通wait_closed确实未结束，再验证测试有界关闭约**1012ms**返回。ruff/pyright全通过。 |
| go-reviewed-final.log | 包含补齐观测的最终Go **8个测试包**、TCP/race/vet/build通过；重新构建与实际链路二进制摘要一致。 |
| source-match-final.log | host与容器Python src/tests、Go cmd/internal无差异，仅排除__pycache__及此前已有的容器审计探针。故障副本不混入正式源码；记录二进制、生产包和关键源文件摘要。 |

464项运行后只新增了上述Next上传和清理两项测试（生产代码未再改变），并另跑通过；23专项与464相关组有重叠，不累加为独立总数。不声称重新运行Python全部测试、前端全量或新Next生产构建；前端源码未变，实际生产包摘要固定。

完整三进程最终SSE首段 **50.717 / 51.448ms**，客户端断开到模型取消 **4.407 / 4.139ms**；同次验证OAuth302、多Set-Cookie、SC历史会话/记忆召回、原生创建、R1/R2以及不同用户/作品拒绝。阈值保持原2秒，未为了变绿扩大窗口。

### 独立双轴复核

- **Standards**：初审0项；补充发现1项P3——测试失败后的wait_closed无界。仅测试清理改为1秒正常关闭、必要时abort后再等待1秒，并收束等待任务；实际阻塞TCP缓冲验证后，独立只读复核确认闭环。没有改生产超时/成功判断。
- **Spec**：发现1项P2验收证据缺口——handler返回不足证明Transport异步上传停止。补active Read+Close实际观测、真实挂起故障注入及Next实际disconnect后，独立只读复核确认闭环。未解决Spec P1/P2为0；没有将协调批准当成独立审查。

代理修复未发现未解决P1/P2；两轴报告在scratch/review-final.md分开记录。限制/范围见下节，不把局部传输修复宣称全仓库所有安全入口已审完。

### 资源和一致性

Go实际版本 **go1.27.3 linux/arm64**。最终二进制11,053,769 bytes，SHA-256 `d4573ba8defb689e306b1872cff5adeec4e2da0945427b48b2ddf72f95b2bf94`；Go复核重建相同。实际Next server.js摘要`3a44d862ae978d8ebe4195c385c5820e04ba413be37d6e5ebe75ffa4a3a7227c`，代理route.js摘要见前述。当前生产Go http.go摘要`794cefb7c829a102a8747299b774ca95b4133130fd98c735b40a26806be6f800`，正式TCP测试摘要`427a354bba84a7a15906646c473632477d9c6c60ffa937da435c311916c22e0c`。

| 既有容器可写层 | 开始bytes | 期末bytes | 净增bytes |
|---|---:|---:|---:|
| Python | 2,953,625,600 | 3,285,864,448 | 332,238,848 |
| Go | 712,876,032 | 755,867,648 | 42,991,616 |

本轮净增 **375,230,464 bytes，约357.85MiB**，含合成临时库、多个二进制、编译缓存和测试副本；并非内存峰值或毛新增产物总量。没有新增镜像、容器、卷、依赖或发布端口；只复用Python/Go容器，Node生产服务在Python容器内运行。宿主机仅新增必要测试/文档和本票scratch（记录时620KiB，报告收尾略增），没有本轮额外/tmp二进制、语言安装或宿主机构建；上一票临时文件保持原样。

收尾只停止本轮启动的两容器，不清理资源。将来经审查可单独删除本轮`/work/eidolon-core-proxy-*`、`/work/proxy-*-20261029*`、`/work/proxy-probes-20261029`、`/work/proxy-origin-*-20261029`等本票明确新建路径及本票scratch；不要删除复用容器整体、既有卷、用户资料或全局prune。镜像/卷本轮没有可清理新增项。

本票实际文件范围：既有`core/internal/catalog/http.go`增加一行控制器调用及注释；新Go TCP回归、新Python实际代理/上传回归、新本报告和core README说明。创建票文件继续保留，其余源码、Schema和前端不改。合并待审清单24个文件保存在本票scratch；全部未暂存、无新commit、HEAD不变。

## 范围与限制

Python仍为唯一业务SQLite写入者，Schema v3未变，Go创建用途凭据和单次/未知结果政策不变。未改Frontend、会话/记忆作用域、玩法、安全协议、OAuth或生产配置。原SC直连文字回合的header/query作品语义不在本票变更范围，初版探针不能假设所有直连入口与Go网关同样处理；如需扩大作用域防护应另票确认，不在传输修复中顺手改变。

只使用Docker现有镜像、容器、独立测试库/凭据；不连接生产/读取真实.env。未测真实生产容量、断电、磁盘耗尽或自定义不支持Unwrap的HTTP/1 ResponseWriter包装器。没有全局请求体读尽；早期拒绝下未发送完body时，客户端断开仍释放实际任务。

## 2026-10-29 协调复核接受与本地提交收尾

维护者经协调复核已接受创建代码、独立代理修复及本地验收，解除此前“待审不提交”的临时限制，授权仅本地Git收尾。采用一个组合提交：两票共享路由、测试与验收文档，创建的最终链路证据依赖代理修复；提交当前已验收的24文件组合，不另造未经验证的拆分中间状态。本次只追加收尾文档，不修改已验收业务源码或重复全量测试。历史未提交、阻塞状态均保留为当时记录。

本地提交完成后的准确SHA与文件核对收据保存在`.scratch/proxy-origin-rejection/local-commit-receipt.md`并在最终交付中报告（提交不能嵌入自己的SHA）。只暂存已审24文件白名单；scratch、日志、数据库、密钥、构建产物、.DS_Store与用户资料不进入提交。无push、PR、生产或部署。后续仅准备删除会话规格，未经规格审查不实施下一票。
