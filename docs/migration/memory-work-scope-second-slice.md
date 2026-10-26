# 第三阶段第二条切片：记忆作品隔离

状态：2026-10-26 开始审计，2026-10-27 完成本地验收；不是生产迁移许可。基线为 `434a3a8e391c4d15d94937c46c738dbdedb44351`，分支 `refactor/multi-work-dlc`。开始时已跟踪文件无改动，既有 scratch 与 docs/.DS_Store 保留；继续工作时发现的未跟踪 docs/ideas/ 也保留并排除提交。

## 实现前入口审计

| 入口 | 当前行为与必须处理的边界 |
|---|---|
| memory.py `_SCHEMA` / `_open` | memories 的 UNIQUE(user_id,text) 阻止跨作品同文本；source_character_id 为作品内裸 ID；conversation 可解除关联，因此作品不能只从现存会话推导。superseded_by 目前无外键；memory_vectors 的 user_id 与父记忆无复合约束。启动 executescript/补列不能用来偷偷升级已有记忆。 |
| `add_carried`、`_add` | 包导入去重和提炼更新均仅按 user/text；失效关联仅按 id/user。现有 `_add` 校验 SC 会话，但会话删除后允许保留无来源记忆，必须在任务开始时保存可信作品身份。 |
| `read_kept` / `_kept_with_vectors` / `_stored_vectors` | 候选文本与向量目前只按 user 读取；必须在 SQL 层加入作品谓词，不能先送向量算法/重排再过滤。 |
| `_nearest` / `_shown` / `_relevant` | Python 内存内计算向量距离；提炼失败的 fallback 也读全部用户记忆。重排只应接收绑定作用域的候选。当前记忆模块没有独立 FTS/关键词 SQL 检索路径，不应把剧情/事实 RAG 当作用户记忆关键词检索。 |
| `MemoryTurn` / `_write` | 保留原 Conversation、模型与 clearings；pending/clearings 只按 user 分组。应增加作品维度，避免清除 Demo 误丢 SC 后台写入；全局注销则要等待/阻断全部作品任务。 |
| retrieval.py / engine.py | Retrieval 使用 MemoryTurn 候选并调用 find，再与静态参考组合；engine.begin 和 write_down 是作用域传递接缝。现有 SC 引擎保持 SC，禁止借本切片开放 Demo 对话。 |
| app.py `/api/memories` | 已登录用户旧 API，无作品参数；明确作为 SC 适配层，保持 JSON 不变。 |
| data_package.py | `_read_export` 调 read_kept；`_import` 调 add_carried，后异步 embed_carried。旧包没有作品维度，只能显式导入/导出 SC，不能自动携带 Demo。 |
| database.py / accounts.py | 单会话删除解除记忆来源；全局注销先 forget 再删 memory_vectors/memories。需保留全局删除，而单作品 clear 仅删该作品。 |
| reembed.py / memory_eval.py | 运维再嵌入和评测直接构造 Memories；应明确 SC 兼容边界，不能全局删掉尚未处理的 Demo 向量。 |
| tests | test_memories、test_reach_outs 有直接 INSERT；删除、数据包、reembed、shared_memory、retrieval 与真实三进程覆盖行为。旧直接 SQL 夹具须显式 SC，不得删除行为断言。 |

## 设计方向（实现与测试前的约束）

- Python 继续唯一业务写入者。记忆本体保存可信 work_id，唯一键改为 user/work/text；向量从父记忆继承作品，查询在 SQL JOIN 处限制作品，避免机械复制作品列。
- 新接口显式绑定 user/work 并验证 work_scopes；旧 Memories/旧 HTTP/旧包作为明确 SC 适配。后台任务在 begin 时核实实际会话并保存作用域；同名角色不能作为跨作品键。
- v2 版本门禁；v1→v2 显式离线升级。保留 v0→最终版本的显式离线事务链，不允许应用启动回填。旧唯一键必须通过受控建新表/复制/替换更新，完整保留 ID、字段、向量、sqlite_sequence 与原始摘要。
- 迁移拒绝未知结构、孤儿向量、跨用户/作品失效关联、异常来源，不猜测修复。备份不覆盖、失败整事务回滚。旧 v1 应用拒绝 v2。
- SC 跨角色共享不变。新增作品只用于存储/检索测试；好感度、礼物、任务、OAuth、WebRTC 和非 SC 聊天保持范围外。

## 环境与证据状态

首次 Docker 检查在申请权限后仍报 OrbStack docker.sock 不存在；没有改用宿主机测试。用户开启 Docker 后，已能启动既有 Python 容器。后续首次失败、复验与资源统计追加于此；本节不表示迁移已经实现或通过。

## 已实现的存储与访问边界

`memory_schema.py` 管理 v2 记忆结构。memories 保留原 id/user/source/conversation/text/created_at/superseded_by，新增无默认值的 work_id 及 UNIQUE(user_id,work_id,text)。work_id 必须在 work_scopes 登记；用户、作品及全局记忆 ID 不可改挂。来源会话和 superseded_by 必须同用户同作品，取代链不能形成环。自动编号与显式负数 ID 的区别沿用插入前探针/插入后判断，不放过负数 ID 或文本唯一键 REPLACE。删除记忆清除向量并连接原失效链。

向量保留 memory_id/user_id/model/embedding 和原 BLOB，不增加 work_id；写入检查父用户，禁止 UPDATE 改挂向量身份。读取通过 JOIN memories 加用户/作品条件，在向量距离计算、embedding 补齐、重排之前限定候选。关键词检索是 WorkMemories 的作用域字面匹配接口，没有引入 FTS 服务，也没有改变 SC 自动检索算法。MemoryTurn.find 会重新在当前作用域解析候选 ID，拒绝调用方带入的外作品正文。

WorkMemories 显式绑定用户与已登记作品；同名角色只在绑定作品中解释。旧 Memories 类是 SC 兼容适配，旧 HTTP/数据包 JSON 保持不变。旧包只导出/导入 SC，reembed 命令也只处理 SC 记忆及 SC 静态内容，不删除其他作品向量。新的内部接口不是非 SC 聊天入口；Go 对未实现运行时的拒绝不变。

后台提炼在 begin 时读取真实父会话核对用户/作品/角色。MemoryTurn 保存原会话，任务状态以 (user_id,work_id) 分组，并在同一 Database 的服务实例间共享。单作品 clear 不影响另一作品的待写任务；清除后的旧任务不能重新写回。全局注销失效所有作品的 pending 任务，再沿原全局删除清除全部作品。来源会话在任务执行中被删后，仍可写入原作品记忆，conversation_id 为 NULL；不会借新选中的同名角色改变作品。

## Schema 升级、拒绝与回滚

- v1→v2 使用显式离线命令；受支持 v0 在同一离线事务中先安装会话作用域再安装记忆作用域。新应用拒绝打开既有 v0/v1 会话库；新空库在 Store 初始化事务中创建最终结构。
- 延续 `BEGIN IMMEDIATE`、一致性 backup API、备份独占创建及 fsync；不覆盖既有备份。重建只涉及 memories，保持外键开启，保留全部旧列与向量；DDL 不使用 executescript。
- 迁移核对所有原表列摘要、所有序列高水位、integrity_check 和 foreign_key_check。sqlite_sequence 按 name 比较，避免把表重建后的内部行序当作数据变化；仍保留从未写入的“无序列行”，以及已删除高编号留下的 seq=900（下一条实际为 901）。
- 已有 memories 可含或不含 superseded_by；后者显式迁移为全 NULL，全部仍有效。没有记忆表或从未写入的空表也有回归覆盖。
- 拒绝未知列、弱类型/非空/主键/唯一键、异常 FK 动作、自定义 CHECK/COLLATE 等额外约束、未知向量触发器、孤儿/跨用户向量、非法来源和失效关系、循环失效链、无效时间。没有删除或猜测修复异常历史。
- v2 启动检查所需表、记忆列及触发器，包含 memory_insert_guard 的形状；损坏辅助表不能带病启动。重复迁移先校验后返回 already_migrated，不创建/覆盖备份。
- 事务故障注入在记忆表重建后抛错，原库 SQL dump 和 user_version 保持 v1，备份也保持 v1 原数据。旧 `434a3a8e` 应用门禁已实际验证拒绝 v2；恢复时必须同时恢复匹配备份和旧应用。

命令仍为（仅操作独立合成/待审批副本，本轮未对生产执行）：

```sh
python -m eidolon.work_schema --offline --database /test/history.db --backup /test/before-v2.db
```

v1 已有可信作品登记保持不变；不能通过这条升级命令或客户端请求临时新增作品。原版本未修复的实验 v1 若缺少前切片必要触发器会拒绝，不能跳过基线要求。

## 失败证据与修复过程

本轮日志全部保留在 `.scratch/memory-scope-20261026/logs/`，不会提交。

| 日志 | 首次实际结果与后续处理 |
|---|---|
| storage-red.log | 2 failed：v1 未升级、绑定接口不存在；随后实现。 |
| storage-first.log | 40 passed、1 failed：旧历史测试在新库重建同名表；改为真实旧 Schema 离线升级，保留历史召回断言。 |
| sql-red.log | 2 failed：SQL 向量改挂与循环失效链；补不可变身份及无环检查。 |
| memory-expanded.log | 142 passed、1 failed：空维护库未初始化版本结构；reembed 走 Store 统一初始化，并保证初始化异常关闭连接。 |
| review-vector-red.log | 弱向量 Schema 未拒绝；增加类型、非空和复合主键验证。 |
| review-gate-red.log | 2 failed、1 passed：v2 缺辅助表仍启动、空旧表产生额外 seq=0；两项均修复。 |
| vector-trigger-red.log | 未知旧向量触发器未拒绝；改为明确拒绝未知结构。 |
| sequence-order-red.log | 记忆序列后还有其他表序列时，物理行序造成摘要误报；改按 name 比较并验证高水位。 |
| extra-constraint-red.log | 自定义旧 CHECK 被表重建丢弃；改为拒绝额外旧约束，原库完整保留。 |
| three-process-first.log | 2 failed：v1 冻结夹具会话身份已不可变，旧脚本仍尝试修改角色；改为夹具初始即含可续聊的 mano，会话约束不移除，来源记忆的已移除角色仍保留。 |
| full-first.log | 2097 passed、3 failed、1 skipped：上述两项三进程失败，加备份故障注入未命中真实数据页。 |
| review-green.log | 105 passed、1 failed：首次固定备份页后，count 查询仍可能用索引；最终先 checkpoint 合成 WAL，再损坏实际 users 根页，以 NOT INDEXED 表扫描确认损坏。原备份拒绝断言全部保留，backup-injection-final.log 通过。 |

独立 Standards 审查无明确规范问题；Spec 审查两项 P2 已分别用红灯复现并修复，最终独立复核没有未解决 P1/P2。未用跳过测试、放宽隔离、增加 timeout 或更改业务期望掩盖失败。

## 最终验收结果

| 范围 | 结果 | 证据 |
|---|---|---|
| Python 全量 | **2107 passed、1 skipped、121 warnings**，65.77 秒；仅 Windows proactor 跳过 | final-acceptance.log |
| 迁移/记忆/数据包/注销/备份/真实三进程专项 | **212 passed、4 warnings**，12.15 秒；包含最后补强的“两作品均有记忆和向量时注销”断言 | targeted-reviewed-final.log |
| ruff、pyright | 通过，0 errors、0 warnings | final-acceptance.log / targeted-reviewed-final.log |
| Go 单元/TCP、race、vet、构建 | 通过，Go 源码未修改 | go-final.log |
| 前端测试 | **77 文件、1158 passed** | frontend-tests.log |
| 前端类型检查、Next 生产构建 | 通过；字体下载临时连接 bridge，结束恢复断网 | frontend-tests.log / frontend-build.log |
| 新构建代理矩阵 | **58 passed、0 failed**，3 轮、2000ms 窗口 | proxy-final.log |
| 源码一致性 | 本地与 Python 容器 121 个源码文件、178 个测试/SQL 文件完全一致；Go/前端源码及依赖清单亦比对一致 | source-match.log 及命令记录 |

最后新增的注销断言只加强测试；完整业务代码已通过全量，之后运行最终专项和静态检查，未再修改业务行为。原 Go↔FastAPI、SC 登录/历史/聊天/数据包/共享记忆/RAG 回归在全量覆盖；三进程使用真实 Next 生产入口、Go 和 Uvicorn，但 OAuth、模型、embedding、reranker 是合成测试服务。

专项 schema 导出为 `schema-final.json`：v2，141 个对象，完整性 ok、外键违规 0。历史冻结夹具保留记忆 9/17、来源已移除角色、NULL 来源、被取代关系与向量 BLOB，升级前后原字段和向量逐值一致。三进程明确检查旧 SC 记忆进入模型 system prompt，Demo 秘密文本不进入，旧 SC `/api/memories` 不暴露 Demo。

## 性能、资源与生产限制

`migration-metrics.log` 的合成探针包含 1000 条记忆和 1000 个 64 维向量：迁移 **38.872 ms**，原库 **630784 bytes**，迁移后 **749568 bytes**，备份 **630784 bytes**。进程峰值 RSS **136980 KiB**，包含 Python 与测试模块依赖，不是迁移增量，更不是常驻生产内存指标。旧 v1 应用实际拒绝 v2，最终完整性 ok、外键违规 0。

候选查询先收窄作品，仍沿用 Python 内存向量距离；关键词使用作用域内字面匹配，不承诺大库全文搜索性能。迁移需要整库备份、记忆表复制、索引重建和多次摘要扫描；Azure 生产库规模、真实向量维数、磁盘吞吐、WAL、并发停写与极端失效链深度均未压测。上线前须独立审批并预留原库、备份、重建临时页及 WAL 空间，不能用这个小样本推算 1GB 机器预算。

没有新增 Docker 镜像、容器、数据卷或宿主机依赖。复用既有 Python/Go/Node 容器；只为字体下载临时接入普通 bridge，没有宿主机端口或提权。保留全部日志、合成库、pytest 临时目录和缓存，不做 prune。宿主机本轮 scratch 约 **504 KiB**（收尾材料会少量增长）。一次中途到结束的可写层观测：Python 2486333440→3134644224 bytes，Go 520749056→520790016，Node 1166102528→1171218432，合计约 **623.2 MiB**；这是观测区间内的容器层变化，不是整个切片总增量，也不等于 OrbStack 虚拟磁盘增量。合成性能库保留在 Python 容器 `/tmp/memory-migration-metrics-tnojya3p/`。清理需审查后仅针对本轮具体目录执行，本轮不删除。

## 交付范围与下一切片

已实现并测试：多作品记忆存储/去重/取代/删除、向量及关键词候选隔离、异步身份保留、v0/v1 离线 v2 迁移和回滚、SC 兼容适配、真实三进程记忆召回。

仅设计/尚未验证：真实生产规模迁移和恢复演练、Azure 停机与容量预算。尚未实现：非 SC 聊天、Go 业务数据库写入、Go 公开记忆/RAG 运行时、好感度/礼物/任务/成就多作品迁移。下一切片建议审计同作品角色状态及奖励幂等键，仍须单独决定，不能据本报告自动启动。

提交范围为本切片 17 个源码、测试和文档文件：database.py、memory.py、memory_schema.py、reembed.py、work_schema.py；test_backup.py、test_memories.py、test_reach_outs.py、test_three_process.py、test_work_schema.py、test_work_memories.py、three_process_backend.py、fixtures/sc-memory-v1.sql；GLOSSARY.md、ADR-0029、SQLite 作用域审计、本报告。排除所有 scratch、日志、数据库、构建产物、.DS_Store 和用户 docs/ideas/。仅允许一个本地提交；其 SHA 见最终交付消息（本报告与实现同提交，避免在文档中写入自引用 SHA）。不推送、不开 PR、不部署。

最终专项中的两组真实三进程 SSE 首段为 **49.174 / 52.830 ms**，实际模型任务取消为 **5.796 / 4.170 ms**；未扩大原观察窗口。延迟来自 `targeted-reviewed-final.log`，与较早阶段测量分别保留。
