# 第三阶段：Go SC好感度查询读模型

日期：2026-10-27；分支refactor/multi-work-dlc，基线 `f941dc648cff9a5d4e0107bb93172b33b7eaf3af`。开工已跟踪工作区干净，原scratch、.DS_Store、docs/ideas/保留。本轮规格与单票在 `.scratch/go-affinity-read-model/`，实际日志同目录logs。仅本地合成数据验收，不是生产部署。

## 实际职责与范围

显式启用 `-affinity-token-file` 后，Go实际计算旧 `GET /api/characters/{id}/affinity` 的默认状态、自然恢复、档位和cooldown_until。不是转发Python算完的Status：私有响应中存储值0仍为0，Go根据观察时间恢复；差分测试还故意使Python公开status函数抛错，Go模式仍成功。

Python保留：Cookie用户认证/同意条款/封禁判断、可信SC身份、角色存在、玩法开关、所有SQLite读取和写入，以及直连旧GET的兼容实现。好感写入、送礼、stats、R2奖励、其他角色业务仍在Python。无非SC玩法开放；Schema保持v3，未修改迁移/经济规则。

未配置Go标志时明确保留旧代理模式供兼容/回滚；配置后错误或合同不符返回通用502，不静默回退旧计算。Python旧公开路由始终保持原行为。这里只迁移一个查询投影，不能声称全部好感度或第三阶段已迁移。

## 安全边界

当前无可复用服务间身份机制，选用单一用途共享凭据文件，不复用用户API key、OAuth或全局secret_key。Python Settings `core_service_token_path` 可通过 `EIDOLON_CORE_SERVICE_TOKEN_FILE`指定，Go标志为 `-affinity-token-file`。文件内容为64位小写hex，常规文件且group/other无权限；未配置Python私有入口404，错误文件阻止配置模式启动。测试仅生成合成凭据，从未读取真实env或生产密钥。

私有 `/api/_core/affinity/{character_id}` 不在OpenAPI中，先常量时间验证服务Bearer，再复用原UserId和sc_request。用户由原会话Cookie解析，客户端user_id不作为身份来源；SC声明冲突拒绝，角色必须来自可信SC内容。Go public router对 `/api/_core`及其子路径所有方法一律404，包括解码后的编码路径，即使客户端持有正确合成服务凭据也不能通过该入口访问原始状态。直连Python仍须同时具备服务凭据和合法会话；没有把“叫internal”或“在内网”当作唯一防护。

Go只向固定私有Python origin请求，不跟随重定向，不使用客户端Authorization作为服务身份；只带本次Cookie/Origin/SC声明和服务端自己的凭据。无用户响应缓存或CookieJar。保留所有Set-Cookie和原认证错误JSON。成功公开响应仅tier/cooldown_until或null，没有数值、内部路径、work/user身份或凭据。内部错误extra字段/null detail、错误版本/作品/角色、超限/无效响应失败关闭，不暴露原始合同。取消上下文传到实际私有上游请求。

## 原始合同与计算一致性

私有version=1响应包含work_id=sc、character_id、enabled、一次服务端observed_at和可空state。state只含存储value、updated_at_us和原cooling；Python仅将有时区的存储时间规范化为精确epoch微秒，不恢复、算档位或预测冷却。无时区异常时间不猜测修复。SQL只读事务绑定认证用户和SC角色；当前affinities仍为SC表，没有借此伪造其多作品Schema隔离。

Go深模块internal/affinity拥有NORMAL=50、THAW=10、每天恢复10以及原档位阈值。负elapsed按0，低值回升至正常值封顶，高值不随空闲下降；cooling只在恢复值低于10时持续。ISO输出保留Python的微秒格式和时区偏移，不把.100000截成.1或把+00:00改成Z。规则与合同版本必须随双方代码配套演进。

固定时钟真实HTTP差分258个状态（阈值两侧、冷却开关、未来updated_at、长时间恢复、随机小数、微秒/无微秒和偏移格式）逐字段一致。缺行trust、关闭null、同用户不同角色、不同用户、合法他人真实ID伪造参数、非法SC声明、未知角色/未登录均有对应证据。不会从仍在的会话猜测用户/作品，也不新增数据库写入。

## 失败证据与修复

| 日志 | 实际结果与处理 |
|---|---|
| model-red.log | Go模型不存在导致编译失败；model-green.log通过实际模型测试。 |
| snapshot-red.log | 新可信配置与私有能力不存在，测试夹具报错；snapshot-green.log验证入口和原始值通过。 |
| private-route-red.log | 首次测试使用含NUL的合成header，只触发502，不能证明上游已访问；不计为命中。 |
| private-route-confirmed-red.log | 修正为合法合成凭据后，固定基线代理实际返回200及合成value，明确命中未封锁问题；当前入口修复后404且上游未触达。基线仅在Docker测试副本暂换，随后恢复。 |
| differential-first.log | 256状态差分及私有入口2项通过；后来补偏移/格式与配置边界到258状态，纳入最终相关回归。 |
| static-first.log | ruff发现7项测试单行多语句，pyright已通过；拆分格式，未改变断言。 |
| null-error-red.log | 独立Spec审查发现私有detail:null被JSON解为零值并401透传；先红灯复现，再显式拒绝为502。 |

独立Standards审查提出错误表t.Run冗余P3与规则常量可读性建议，已改为带status/body标识循环和命名常量，断言/清理保留。两项独立最终复核均无未解决规范问题或P1/P2。测试取消观察通道与清理通道独立，清理不会制造取消通过信号。

## 本轮实际验收

| 范围 | 结果 | 证据 |
|---|---|---|
| Go全包单元、TCP、race、vet、build | 通过；EIDOLON_TCP_TESTS=1显式启用 | go-final-reviewed.log |
| Go实际取消 | 私有上游观察到context取消，原TCP代理before_headers/during_sse通过 | go-tcp.log |
| Python相关回归 | **213 passed、14 warnings**，38.99秒 | python-related.log |
| Python全项目ruff/pyright | 通过，**0 errors、0 warnings** | static-final.log |
| 真实三进程 | 包含于213项；2组assets配置、Go原生投影、R1/R2、认证/历史/记忆/SSE均通过 | python-related.log |
| 固定时钟差分 | **258种状态**，计为一个集成测试，不虚增pytest通过数 | python-related.log |
| 提交前受影响检查补跑 | **36 passed、15 warnings**，3.17秒 | affected-extra.log |

三进程使用本轮Go二进制、原R2已验收Next生产包和真实Uvicorn，外部OAuth/模型为合成。首段SSE **53.056 / 48.573 ms**，模型取消 **7.591 / 4.922 ms**。Go独立私有读取取消在go-tcp.log测得 **85.75µs**，只是本地单次测量，不当作性能保证。

本轮按实际变更运行20个相关Python测试文件（认证/配置/好感/SC边界/Go目录/R2/记忆及三进程），没有重复跑Python全量；前端源码未改，未重跑前端单元/类型/构建。R2的2133/1162历史通过数不混入本轮结果。数据库Schema/迁移未改，未运行生产或真实用户数据迁移。

实际命令（对应Docker容器工作目录）：

```sh
# /work/core
EIDOLON_TCP_TESTS=1 go test ./...
EIDOLON_TCP_TESTS=1 go test -race ./...
go vet ./...
go build -o /work/eidolon-core-affinity ./cmd/eidolon-core
# /work/backend；具体20文件清单见命令记录与python-related.log
EIDOLON_CORE_BINARY=/work/eidolon-core-affinity EIDOLON_AFFINITY_BINARY=/work/eidolon-core-affinity EIDOLON_NEXT_SERVER=/work/r2-next-standalone/server.js .venv/bin/pytest -q -s tests/test_go_affinity.py tests/test_affinity_snapshot.py tests/test_three_process.py tests/test_go_catalog.py tests/test_sc_play_scope.py tests/test_affinity.py tests/test_affinity_cooldown.py tests/test_affinity_prompt.py tests/test_affinity_turns.py tests/test_affinity_stats.py tests/test_affinity_dashboard.py tests/test_stats.py tests/test_sessions.py tests/test_health.py tests/test_llm_config.py tests/test_live_config.py tests/test_live_config_limits.py tests/test_live_config_services.py tests/test_grant_idempotency.py tests/test_work_memories.py
.venv/bin/ruff check src tests --extend-exclude tests/test_state_reward_audit_probe.py
.venv/bin/pyright --pythonpath /work/backend/.venv/bin/python
```

ruff只排除保留在容器的旧诊断探针；它非仓库正式测试。没有降低业务预期、跳过正式失败测试或扩大取消窗口。

提交前执行 `sh scripts/affected-tests.sh --list f941dc648cff9a5d4e0107bb93172b33b7eaf3af`，结果保存为affected-list.log。依照隔离约束，在Docker执行其列出的测试及静态检查；补跑此前20文件之外的test_activity_rollups.py、test_call.py、test_embedding_config.py，36项通过。两批共 **249 passed，0 failed，0 skipped**；警告分别为14/15项，均为既有依赖弃用提示。此为相关测试总数，不是全量测试数。

## 文件、资源与剩余责任

源码：Go main、catalog路由、internal/affinity（纯模型、有限HTTP合同和凭据读取）；Python affinity_auth、affinity原始只读快照、app隐藏路由、Settings可选文件路径。测试：Go模型/HTTP/文件/公共私有路径，Python原始快照/差分，原三进程夹具与断言。文档：core README、ADR-0029、本报告；规格和单票/日志留scratch。

复用既有Go/Python容器，未新增镜像、容器、卷、依赖安装或宿主机工具；Node容器未启动。本轮二进制另存/work/eidolon-core-affinity，旧R2二进制和生产包保留；测试凭据与数据库仅在合成临时目录。不删除旧scratch/缓存/日志；结束后停止本轮启动的既有容器。开工未采集可写层基线，因此不伪报净磁盘增量；期末占用与源码一致性在收尾记录追加。

需要后续单独审批的事项：部署服务凭据分发/轮换及启用开关、真实生产数据和容量验证。源码支持不是生产启用许可。Python仍拥有所有SQLite和写业务，直连公开计算作为显式回滚路径，双方版本必须匹配。没有启动stats/gifts、角色作品键或非SC聊天迁移。

### 收尾核对

- Python容器src/tests与本地逐文件比较一致（仅排除__pycache__和容器保留的旧诊断探针）；Go容器cmd/internal/go.mod/go.sum与本地一致。
- Go与Python容器实际使用的`/work/eidolon-core-affinity`摘要均为`be8be45a124141fd765e7ce850e20e8541c75302912d2ba96c0bebb0d3cd1cb9`。
- 补跑后的期末容器可写层：Python **2,648,305,664 bytes**，Go **555,225,088 bytes**（resources-final.log）；这是含既有依赖和历史产物的总占用，不是本轮新增量。本轮未测内存峰值。
- 宿主机新增源码、正式文档和`.scratch/go-affinity-read-model/`证据目录；该scratch核对时为 **2144 KiB**，后续日志/票据收尾会有少量增长。未在宿主机安装依赖或编译，未新增Docker镜像、容器或数据卷。
- 不自动清理：日后可先审查再单独移除两容器中的`/work/eidolon-core-affinity`及本轮合成临时测试目录、宿主机本票scratch；这些位于复用容器/项目内部，不应删除整个容器或卷来清理本轮产物。
