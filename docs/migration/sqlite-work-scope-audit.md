# SQLite 作品作用域审计（第三阶段第一条切片）

审计日期：2026-10-26。仅检查源码及由测试夹具创建的数据库，未打开真实用户库。完整建表、索引、外键、视图、触发器 SQL 来自实际启动的测试应用，记录在 `.scratch/db-scope-20261025/schema-after.json`；直接 SQL 访问点清单为同目录 `direct-sql-inventory.txt`。迁移前核心 Schema 固定于 `backend/tests/fixtures/sc-store-v0.sql`（从 HEAD 的 Store Schema 提取）。这不是生产数据快照。

下面逐项列出所有应用表；每行“入口”模块负责其读写/维护。跨模块的导入、删除及后台路径另列。PK 为主键，U 为唯一约束；精确复合索引顺序、FK、CHECK 和既有触发器以保存的 SQL 为准。

| 表 | 主键/关联及当前边界 | 本轮处理、入口与下一切片风险 |
|---|---|---|
| users | PK id；全局账号 | 保持；store/accounts/account_api，删除跨作品是账号删除的既有语义 |
| sessions | PK token_hash，FK user；用户/到期索引 | 全局登录保持；store/sessions |
| sign_in_methods | U(provider,subject)，FK user；user 索引 | 全局 OAuth 保持；store/accounts，不能按作品复制身份 |
| producer_profiles | PK/FK user | 现有称呼、时区、生日为共享用户资料；store/producer/data_package；后续如增加作品角色称呼须另建边界 |
| banned_identities | PK identity_hash | 全局封禁，store/accounts；不含可复用裸身份 |
| conversations | PK id，FK user；原 user/character/time 索引 | 新 work_id FK work_scopes；用户+作品+local character 身份；store 的旧接口仅 SC，WorkConversations 绑定用户/作品 |
| messages | 自增 PK id，FK conversation；conversation/id 索引 | 从父会话严格继承作品；禁止改挂其他父会话；store/WorkConversations；不重复存 work_id |
| conversation_events | 自增 PK id，FK conversation；conversation/time 索引 | 结束/赠礼/挂断仍 SC，触发器拒绝非 SC 父；store/happenings |
| photos | PK id，U/FK message，FK conversation；conversation 索引 | 保持 SC；新增两条父引用须匹配的检查；store/photo 生成后台与 data_package |
| calls | 自增 PK id，FK conversation；conversation 索引 | 从父继承但业务仍 SC，触发器拒绝非 SC；store/happenings/call |
| reach_outs | 自增 PK id，FK conversation；conversation/time 索引；record_id 无 FK | 仍 SC；store/reach_outs，活动记录允许过期，不能误补强为永久 FK |
| reach_out_schedules | PK/FK conversation | 仍 SC，父作用域触发器；reach_outs 调度仅看 SC 当前会话 |
| reach_outs_off | PK/FK user | 当前是用户关闭 SC 搭话；reach_outs；未来作品开关需要重新定义 |
| memories | 自增 PK id，FK user、可空 FK conversation；U(user,text)；conversation 索引 | 仍 SC：去重、跨角色共享均只在 SC 业务中；有关联父时检查 SC+同用户；孤立历史记忆保留为 SC，不因删除会话丢弃。memory/data_package |
| memory_vectors | PK(memory_id,model)，user/model 索引；当前无 FK | 仍 SC；memory 负责关联、删除、重嵌入；未来需作品约束、去重和向量查询一并迁移，不能仅加字段 |
| affinities | PK(user,character)，FK user；裸角色 | 仍 SC；affinity/happenings/engine，未来须(user,work,character)；本轮新仓储不调用它 |
| gift_balances | PK/FK user | SC 抽取/重置卡余额；gifts/happenings，下一切片明确作品归属 |
| gift_backpacks | PK(user,gift)，FK user；裸 gift ID | SC 背包；gifts，未来作品内礼物身份 |
| gift_rewards | PK(user,key)，FK user | SC 奖励幂等记录；gifts/happenings；key 必须作品内隔离 |
| gifts_opened | 单例 id=1 | SC 玩法开关时点；gifts，不应成为未来全部作品共同开关 |
| reset_card_grants | 无 PK，granted_at/count | SC 全体用户补发记录；gifts；下一切片需作品作用域 |
| daily_task_progress | PK/FK user | SC 每日回合/赠礼进度；happenings；未来(user,work) |
| achievement_progress | PK(user,achievement,character)，FK user；裸角色 | 仍 SC；achievements 的历史回填改查 SC 视图，不计入其他作品消息 |
| achievement_stages | PK(user,achievement,character,stage)，FK user | 仍 SC；achievements，未来作品维度及奖励幂等同步迁移 |
| achievements_launched | 单例 id=1 | SC 上线回填标记；achievements；未来按作品标记 |
| data_package_imports | user/at 索引，无 FK | SC 数据包导入频控；data_package。旧格式无作品字段，不接受新作品状态；未来必须升级包格式 |
| usage | 自增 PK id，FK user；user/kind/time、time 索引 | 用户级服务额度保留；usage/engine/call/photo；未来需明确全局额度与作品统计的区别 |
| user_limits | PK/FK user | 全局用户额度及管理备注；usage/admin，保持 |
| activity_records | 自增 PK id，可空 FK user，裸 character，call_id/ended_conversation 非 FK；time/user 索引 | 当前 SC 活动记录及匿名化；activity；未来需要作品身份，但本轮仓储测试数据不触发对话活动 |
| service_calls | 自增 PK id，record_id 无 FK；time/record 索引 | 全局服务调用账目，活动行过期可独立保留；activity，无用户正文 |
| record_rollups | 多列复合 PK，含裸 character，WITHOUT ROWID | SC 历史聚合；activity；未来需作品维度，不混算新的业务数据 |
| taken_rollups | PK start | 全局调用总量；activity；是否按作品分账另设计 |
| user_rollups | PK(length,start,who,cooled)，who 索引，WITHOUT ROWID | 用户/匿名历史统计；activity；下一切片区分全局统计与 SC 状态 |
| call_rollups | 多列服务/模型/时间复合 PK，WITHOUT ROWID | 全局成本聚合；activity，保留 |
| spread_rollups | 多列分布统计复合 PK，WITHOUT ROWID | 全局延迟/服务统计；activity，保留 |
| episodes | PK id | SC 剧情语料；corpus；未接入第二作品剧情 |
| episode_cast | PK(episode,character)，FK episode CASCADE，character 索引 | 裸 SC cast；corpus；未来父作品或复合角色身份 |
| episode_lines | PK(episode,seq)，FK episode CASCADE | 从剧情父记录继承；corpus，未来先迁移 episode |
| episode_passages | PK(episode,seq)，FK episode CASCADE | SC 剧情向量；corpus；需与检索过滤同步 |
| story_corpus_version | 单例 id=1 | SC 语料版本；corpus；未来每作品版本 |
| embedded_texts | PK(scope,model,text) | 静态文本向量缓存；embedded_texts，不是用户记忆；scope 尚非作品安全边界，未来须命名空间 |
| config_overrides | PK name，value/last4 | 全局管理员配置；live_config，禁止在本轮日志输出实际配置值 |
| price_overrides | PK(provider,model,unit) | 全局价格覆盖；prices |
| price_catalog | PK(provider,model) | 全局价格；price_catalog |
| price_catalog_thinking | PK(provider,model) | 全局推理能力；price_catalog |
| price_catalog_hosts | PK(host,provider) | 全局服务映射；price_catalog |
| price_catalog_providers | PK provider | 全局供应商；price_catalog |
| price_catalog_offers | PK(provider,model) | 全局模型地址/协议；price_catalog |
| price_catalog_state | 单例 id=1 | 全局刷新状态；price_catalog |
| announcements | PK id | 全站公告；announcements，保留 |
| sponsor | 单例 id=1 | 全站赞助图；sponsor，保留 |
| backup_runs | PK id | 整库备份运维记录；backup，不按作品拆分 |
| work_scopes | PK id NOT NULL | 本轮新增；离线可信 Go Registry ID 白名单，不提供 HTTP 注册接口 |

SQLite 的 `sqlite_sequence` 是内部自增序列，不属于业务表，也不分作品。迁移不重建消息表、不重新分配任何 ID。

## 实际调用链与边界

- HTTP/API：现有认证得到 user_id，`conversation_or_404` 通过旧 Store 的 SC 视图查会话并校验用户，再进入 engine/call。显式非 SC header/query/body 仍拒绝。没有公开新的多作品对话路由。
- 新仓储：`WorkConversations(db, user_id=..., work_id=...)` 无作品默认值；创建受 works FK/触发器约束；列表、详情、消息、改名、写消息、删除均在同一读/写事务内校验用户和作品。
- 旧入口：Store 显式插入 `sc`；会话读、当前/最近/统计、照片读取、导出、导入去重均限定 `sc_conversations`。视图保留 rowid 作为相同时间戳的旧排序依据，公开 dataclass/JSON 不新增字段。
- 写回：engine 的 add_turn 使用 SC 父校验；赠礼、挂断、通话记录、搭话和记忆父关联有 SC 触发器；记忆后台写回额外拒绝仍存在但不属于同用户 SC 的父。删除后记忆的 conversation_id 仍可为 NULL。
- 清理：`delete_user_data` 故意清理全局账号的所有作品会话及消息；单会话删除先在仓储/服务定位用户与作品，才执行现有子表删除、记忆断关联。没有把账号删除错误改成只删 SC。
- 数据包：旧 JSON 格式保留，只导出 SC 视图，导入明确创建 SC，原相同角色/时点去重只看 SC。未来第二作品包不能套用此格式。
- 运维：Database 是统一业务入口，打开已有 v0 会话库会拒绝；离线迁移不构造 Settings，不读取 `.env`。健康只读、SQLite 一致性备份、恢复仍走既有专用连接。维护 CLI 使用 Database 时同样受版本门禁约束。
- BYOK：用户自己的 API Key 继续在浏览器，不在这些表新增持久化；旧 own_keys/access_keys 遗留版本不由本轮新迁移擅自清理。仅支持报告列出的旧核心形状，其余未知 Schema 应先用匹配旧版本和独立备份处理。

## 为什么没有给所有表加 work_id

会话 ID 继续全库唯一，消息和子业务表能通过不可变的父身份确定作品；重复列反而会产生双份身份。记忆可能脱离原会话，且当前唯一约束是(user,text)，不能安全从会话推导所有记忆；好感度、背包、成就和剧情缓存也有各自业务唯一键。因此它们明确留在 SC，下一条切片必须同时迁移键、查询、导入导出、删除和后台作业后，才能开放第二作品运行时。

## 2026-10-26 负数消息编号修复补充

新增辅助表 `message_insert_guard(id INTEGER PRIMARY KEY)`，登记于 `NOT_USER_DATA`。它仅暂存本次 INSERT 前可能冲突的全局消息编号，没有正文、用户、作品或会话字段。BEFORE INSERT 清空并捕获冲突，AFTER INSERT 按实际分配的 ID 判定替换并 `RAISE(ABORT)` 回滚整条语句；普通插入清空辅助表。`OR IGNORE` 可能跳过 AFTER，下一次 BEFORE 会清理遗留状态。SQLite 单写者串行化与事务隔离保障该探针不被并发写入混用，不要求 `recursive_triggers` 开启。

没有跳过负数 ID、重建 messages、重分配历史 ID 或取消父会话约束。新增表和探针触发器纳入启动校验，旧实验版 v1 不会被静默视为已修复；本阶段尚未部署，需从原 v0 合成库/匹配备份执行最终迁移，不在应用启动时修补旧实验版本。上文旧 schema 对象计数保留为原验收记录，不能作为此次最终对象数量。

## 2026-10-27 记忆领域 v2 增量审计

上文“记忆仍限 SC”描述第一条切片历史状态。本切片将 memories 改为显式用户/作品所有权，新增 work_id、UNIQUE(user_id,work_id,text)、memories_by_scope；来源会话、取代关系均由 SQL 约束为同用户同作品。记忆 ID、作品和用户不可 UPDATE/REPLACE 改挂。辅助 memory_insert_guard 只捕获候选冲突 ID，已登记 NOT_USER_DATA；它处理自动编号 -1 哨兵与真实负数 ID 的区别。

memory_vectors 保留原字段和 BLOB，通过 memory_id 父记忆继承作品；所有候选及再嵌入查询按作品限定，父用户校验和向量身份触发器拒绝改挂。删除记忆会清除其向量并修复取代链，整作品 clear 只影响绑定作用域；全局账号删除仍覆盖所有作品。表重建不迁移好感度/礼物/任务/成就/剧情缓存。

数据包 read_kept/add_carried、旧记忆 HTTP、SC 引擎以及维护 reembed 均显式走 SC 适配；reembed 不会处理或删除 Demo 向量。新关键词入口是作用域 SQL 的字面匹配，不是新增全局 FTS 服务。真实三进程夹具从冻结 v1 记忆库升级，验证原 SC 记忆进入模型提示词而 Demo 文本不进入。

新版本门禁、v1/v0 迁移范围、异常 Schema 拒绝与首次失败记录见 [第二条切片报告](memory-work-scope-second-slice.md)。原统计及原始失败日志保留，不覆盖历史结果。
