# 第三阶段第一条切片：SQLite 会话作品隔离与离线 SC 迁移

验收日期：2026-10-26。仅使用 Mac 项目源码和 OrbStack 隔离容器中的虚构数据；未访问任何真实用户数据库、生产服务或 `.env`。

## 基线和修改边界

开始与结束均在 `refactor/multi-work-dlc`，HEAD 为 `5cec1ad4f3d8353653b73364326702d2c06af5e2`。暂存区为空。第二阶段三条内容切片仍未提交；本轮开始的 `git status` 和已跟踪差异分别保存在 `.scratch/db-scope-20261025/status-before.txt`、`tracked-before.diff`。目录名沿用开始时日期，最终验收跨至 10 月 26 日。

本轮新增：

- `backend/src/eidolon/work_schema.py`：版本门禁、可信作品登记、事务 DDL、离线备份与迁移、验证。
- `backend/src/eidolon/work_conversations.py`：显式绑定用户/作品的会话和消息存储接口，不是新产品聊天接口。
- `backend/tests/fixtures/sc-store-v0.sql`、`test_work_schema.py`、`test_work_conversations.py`。
- 本报告和 `sqlite-work-scope-audit.md`。

本轮修改原有 `data_access.py`、`database.py`、`store.py`、`memory.py`、`reach_outs.py`、`achievements.py`；修改 `test_achievements_launch.py`、`test_store_at_scale.py`、`test_conversation_titles.py` 的夹具，保留原行为断言；在第二阶段未提交的 `test_three_process.py` 和 `three_process_backend.py` 上增加迁移历史与跨作品/用户验证；向 ADR-0029 追加决策。

第二阶段的内容发布、Go TOML/世界/角色/礼物、公开目录、Dockerfile、依赖、前端及其历史报告不是本轮新增。本轮没有修改 Go、Next 运行时代码，没有执行暂存、commit、push、merge、PR 或部署。

## 数据依赖审计与设计

完整逐表审计见 [SQLite 作用域审计](sqlite-work-scope-audit.md)。该文档列出全部应用表的主键、关联、作用域、裸角色风险、CRUD/后台/维护入口和延期理由；精确 DDL、索引、触发器另存 `schema-after.json`，直接 SQL 位置存 `direct-sql-inventory.txt`。原系统没有统一版本号，各模块启动时会自行建表、补列和移除过时结构；不能直接把本次迁移藏进那些启动逻辑。

关系变化：

```text
原：全局 users → conversations(id, character_id) → messages(conversation_id)
新：全局 users → conversations(id, user_id, work_id, character_id)
                         ↑ work_scopes(id)           ↓
                                        messages(conversation_id)
```

会话 ID、消息 ID 继续全库唯一；`character_id` 保留旧字段名，语义是 local_character_id。角色身份由 `(work_id, character_id)` 决定，不把裸角色当全局身份；同一用户/作品/角色可以有多段会话，因此不会给三元组加错误的唯一约束。旧 SC URL 和公开 JSON 不变，不把内部 work_id 暴露进旧 dataclass。

消息只从父会话继承用户与作品，不重复存 work_id。父会话的 id/user/character/work 不允许 UPDATE；会话和消息都禁止 `INSERT OR REPLACE` 替换已存在的身份，消息不允许换父。所有业务仍经过 Python 单写线程（ADR-0028）；Go 不持有 SQLite 连接。

新作品只能由部署者提供的可信 Go `/api/works` 清单离线登记；新库默认只登记 SC。新仓储创建时受登记表与身份校验约束。没有 HTTP 作品注册或非 SC 聊天入口。存储层允许虚构 Demo 同名角色作隔离测试，不等于产品已开放它。

## 迁移版本与原子性

使用 `PRAGMA user_version`：0 表示未版本化的旧会话库，1 表示本切片。它管理会话身份契约，不宣称所有 SC 业务表已迁成多作品 Schema。

- 新应用在 `Database` 打开已有 v0 会话库时明确拒绝，提示离线迁移与备份；不自动回填。
- 新数据库由 Store 的受控事务逐条执行 DDL，再安装作用域结构和版本号。不能使用 `executescript` 提前提交。故障后所有新表回滚，重新初始化可以成功。
- 只支持固定旧核心列集（含尚无 title 的历史变体），检查字段类型、非空、默认值、主键、核心 FK、消息 role CHECK 和自增约束；未知版本/形状拒绝。残留旧 access_keys/own_keys 的更早账户版本也拒绝，不能借新版本启动删除其数据。
- `BEGIN IMMEDIATE` 阻止其他写入，先验证旧数据，再创建不覆盖既有文件的 0600 备份，最后迁移、核验并提交。数据库连接始终开启外键；没有关闭外键重建表。
- 备份使用 SQLite backup API，包含已提交 WAL；新备份完整性及原有列摘要必须与锁定源一致，关闭目标连接后 fsync。
- 使用 ADD COLUMN 避免重建被引用的会话表。DDL 中 work_id 因 SQLite 对已有行/FK 的 ADD 限制为可空，但 BEFORE INSERT 强制要求已登记的非空作品，身份 UPDATE/REPLACE 禁止绕过；历史回填后检查每行严格为 sc。新写入没有 SC 默认值。
- 对所有原有表/列及 sqlite_sequence 计算有序摘要，迁移后重新比较，防止旧触发器顺带改正文、时间、关联或自增序列。摘要不输出用户内容；扫描内存按单行大小增长，不把整库载入内存。
- integrity_check 必须为 ok，foreign_key_check 必须为空。失败回滚全部本轮 DDL、回填、版本；保留备份与原数据，不删异常记录。

增量结构：新增 `work_scopes` 表、`conversations.work_id`、`conversations_by_work(user_id,work_id,character_id,created_at)`、`sc_conversations` 兼容视图，以及作用域/不可变身份/禁止替换/SC 子业务/照片双父一致性触发器。保留所有原表、已有索引和消息自增序列。旧无 title 的受支持变体在同一离线事务中补 NULL title，不虚构旧标题。

## SC 历史回填与异常处理

所有旧会话明确映射 `work_id='sc'`；原 ID、用户、local character、时间、标题、消息正文、顺序及关联保持。已从角色配置移除但格式有效的历史角色仍保留，不要求历史身份必须在当前角色目录里；历史可读，继续聊天仍遵循现有角色存在检查。

孤儿、空/非法会话身份、冲突主键/弱约束、外键冲突、照片与消息父记录不一致、记忆父用户不一致、无时区/无效的核心历史时间戳均拒绝，不删除或猜测修复。迁移器没有声称覆盖任意年代或任意第三方改造的 Schema；不符合支持形状的库需独立处理。

测试固定旧库为 1 用户、1 会话、1 消息（ID 7，Unicode 原文，固定 UTC 时间）；回填前后这些数量和原列逐值不变。WAL 测试另加一条已提交但尚在 WAL 的消息，备份中两条均在。实际三进程测试让该历史用户通过模拟 GitHub 身份重新登录、读取旧 URL 并续聊，随后创建新 SC 会话。

## 存储、服务与 HTTP 隔离

`WorkConversations` 必须同时传 user_id/work_id，没有隐式作品默认值。详情、列表、消息、改名、消息写入、删除的归属检查均在同一事务内完成，未授权记录统一 NotFound。作品身份校验来自服务端登记表，不由客户端临时创建。

旧 Store 是明确 SC 兼容边界：所有会话读取和聚合改查 SC 视图；创建/数据包导入明确写 sc；名字、照片、当前/最近记录、历史导出和去重保留旧行为。视图保留原 rowid 排序的语义。用户全局删除仍清除该用户全部作品，单会话删除不会影响其他作品或用户。

测试同时构造用户 A 的 SC:mano、Demo:mano 和用户 B 的 SC:mano，验证读取、更新、删除、追加消息、父身份 UPDATE/REPLACE 的隔离；未知作品拒绝。原生 SQL 子表约束、绑定仓储、旧 SC Store、真实 HTTP 链路都有断言。数据库文件本身不是面向不可信用户的权限系统；任意持有文件写权限并删触发器/关闭约束的进程不在应用安全边界内。

## 未迁移模块与下一切片

记忆仍以 `(user,text)` 去重、跨 SC 角色共享，向量和来源角色仍是 SC；好感度 `(user,character)`、礼物、背包、每日任务、成就、剧情和静态向量缓存均未改为多作品。其新对话入口依旧拒绝非 SC；会话子业务在 SQL 层也拒绝非 SC 父，记忆写回校验父用户/作品。成就历史回填和所有旧历史聚合只看 SC 视图。

下一条切片应协调迁移记忆唯一键/向量检索/删除、好感度与奖励幂等键、数据包格式和后台处理，再考虑开放第二作品运行时。不能仅给会话加字段就启用 Demo/Gakumas 聊天。本轮不改算法、不接 Go 写入、不迁 OAuth/WebRTC。

## 离线操作与回滚方案（未对生产执行）

在独立迁移容器中挂载受控数据库目录，停掉所有应用和维护写者，明确选择对应版本及可信 Registry 导出：

```sh
python -m eidolon.work_schema --offline \
  --database /data/eidolon.db \
  --backup /backups/eidolon-before-work-v1.db \
  --registry /release/works.json
```

只需 SC 时省略 registry。`--offline` 是操作者停写确认，不会替操作者停止旧服务；事务锁只能约束迁移期间写入，不能阻止旧程序在提交后继续读。必须由单独部署流程保证版本切换。

重复迁移 v1 会校验并返回 already_migrated，不覆盖备份，也不追加作品。已有备份路径直接拒绝。若备份阶段中断，可能留下不可用的新备份文件；原库不迁移，需独立验证并另选新备份路径，不把存在文件当备份成功。

区分两种回滚：迁移中异常由事务回滚；提交后需要停写、按既有 restore 流程恢复完整一致性备份并切回匹配旧应用。旧应用不理解 v1，旧无作用域 INSERT 会被触发器拒绝；**不要依赖旧程序自觉识别 user_version，也不能让旧程序读取混合作品数据库**。新版发现恢复后的 v0 库会再次拒绝启动，除非重新离线迁移。没有提供危险的通用反向 SQL。

已验证备份恢复原始记录和版本、旧写入拒绝、新版旧库门禁。未模拟断电、文件系统损坏或生产停机部署；生产迁移仍需单独备份演练、磁盘预算和审查批准。

## 失败、修复及复审证据

日志目录 `.scratch/db-scope-20261025/logs/`，全部保留：

- `startup-red.log`：旧库打开未被拒绝；实现门禁后通过。
- `storage-first.log`：新仓储方法名 list 遮蔽类型注解，修正为 list_conversations；未修改业务期望。
- `full-first.log`：13 failed / 2047 passed / 1 skipped。12 项夹具直接写会话缺显式 work_id；1 项原测试在新版表上删除 title 被兼容视图阻止。改为显式 SC 夹具和真实旧 Schema 离线迁移后续聊。
- `contracts-first.log`：3 项历史续聊失败，定位为新造夹具的无时区时间戳；修正为旧应用实际格式，并新增无时区历史拒绝测试。
- `contracts-second.log` 与静态日志：一次新增记忆归属检查放错函数导致未定义 db；移动到同事务的持久化函数，未忽略检查规则。
- 规范复审与规格复审均发现新库初始化的隐式提交问题；规格复审另发现旧约束校验不足。`review-red.log` 的 4 项红灯逐一命中；`review-green.log` 为 19 passed。修复后两路只读复核确认原 P2 已解决，未报告新实质问题。循环断言标识 P3 也已补齐。
- 最后原生 SQL 检查发现 REPLACE 绕过 UPDATE 保护；`replace-both-red.log` 分别复现会话、消息两项越界。新增 BEFORE INSERT 拒绝替换既有身份，不仅检查 UPDATE。

此前通过结果都保留为阶段性结果；最终结果以下节最后一次日志为准。

## 完成判定和生产风险

已实现并验证：版本化的会话身份契约、显式离线迁移与一致性备份、SC 回填、原列无损核验、绑定用户/作品的会话消息仓储、旧 SC 服务/API 兼容和非 SC 拒绝、故障回滚与重启恢复。

尚未实现：所有记忆/好感度/玩法表的多作品迁移、第二作品对话、Go SQLite 写入、生产迁移。仅设计：下一切片的多作品记忆与玩法边界。

本轮可供代码审查，不代表整个第三阶段完成，也不是生产迁移许可。未来 1GB Azure 上没有新增常驻进程或连接池，仍使用原单写者；但多一个索引、触发器检查、启动校验，以及离线备份/多次全库摘要扫描会增加磁盘、I/O 和停机时间。尚未测生产规模耗时、峰值 RSS、磁盘临界容量或并发部署；至少预留完整备份、索引增长和 WAL 空间，不能据小型夹具推断 1GB 部署预算。

建议先单独审查并保存第二阶段三个内容切片，再保存本轮数据库切片，建议提交说明：`feat(storage): add offline SC migration and work-scoped conversations`。本轮源码、测试及两份数据库文档/ADR 属于该提交；scratch、日志、临时二进制、`.DS_Store` 均排除。这里只给建议，没有执行 Git 写入。

## 最终验收统计与复现命令

最终日志均已复制到 `.scratch/db-scope-20261025/logs/`；下面结果来自最后包含 REPLACE 防护的源码。

| 项目 | 实际结果 | 日志 |
|---|---|---|
| Python 全量 | **2078 passed、1 skipped、121 warnings**，62.05 秒；仅跳过 Windows proactor | `python/full-final-with-replace.log` |
| 数据迁移与仓储专项 | **21 passed、2 warnings** | `python/storage-final.log` |
| 迁移/三进程/Go↔FastAPI/全部内容契约 | **44 passed、2 warnings**，13.55 秒 | `python/integration-final-with-replace.log` |
| ruff | 通过 | `python/ruff-final.log` |
| pyright | 0 errors、0 warnings | `python/pyright-final.log` |
| 离线命令探针 | 停写确认、Registry JSON、实际迁移、幂等重试通过 | `python/cli.log`；可复现脚本 `cli-probe.py` |
| Go 单元与 TCP | 14 个顶层测试，4 个测试包通过；命令包无测试 | `go/unit-tcp.log` |
| Go race / vet / gofmt / Linux amd64 CGO=0 build | 全部成功，格式/静态/构建无诊断 | `go/race.log`、`vet.log`、`gofmt.log`、`build.log` |
| 前端完整测试 | 77 文件、**1158 tests passed** | `node/tests.log` |
| TypeScript / Next 生产构建 | 成功 | `node/typecheck.log`、`build.log` |
| Next→Go→HTTP 代理矩阵 | **58 passed、0 failed**，3 轮，2000ms 窗口 | `node/proxy.log` |
| 差异检查 | `git diff --check` 通过，暂存区为空 | 最终只读核对 |

实际新库包含 54 张表（含 sqlite_sequence，即 53 张应用表）、55 个索引对象（含自动唯一索引）、20 个触发器和 1 个视图，合计 130 个对象。全部对象定义保存在 schema-after.json；完整性 ok，外键违规 0。本轮新增 19 个触发器，已有活动汇总触发器保留。旧核心冻结夹具有 11 张业务表；迁移前后示例数量详见 cli.log。没有把空测试库结果当成真实生产库检查。

命令在相应容器目录执行，不在 Mac 安装依赖或运行语言构建：

```sh
# /work/backend
.venv/bin/ruff check .
.venv/bin/pyright --pythonpath .venv/bin/python
PYTHONUNBUFFERED=1 EIDOLON_NEXT_SERVER=/work/next-standalone/server.js \
  timeout 550 .venv/bin/pytest -n 4 -q -rs
EIDOLON_SCHEMA_AUDIT=/results/db-scope/schema-after.json \
EIDOLON_NEXT_SERVER=/work/next-standalone/server.js .venv/bin/pytest -q -s \
  tests/test_work_schema.py tests/test_work_conversations.py tests/test_three_process.py \
  tests/test_go_catalog.py tests/test_character_snapshot.py \
  tests/test_content_snapshot.py tests/test_content_release.py
# 两个集成命令同时使用容器已有 EIDOLON_CORE_BINARY=/work/eidolon-core。
# /work/core
EIDOLON_TCP_TESTS=1 go test -v ./...
EIDOLON_TCP_TESTS=1 go test -race ./...
go vet ./...
gofmt -l cmd internal
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -o /results/eidolon-core-amd64 ./cmd/eidolon-core
# /work/frontend
npm test
npm run typecheck
npm run build
EIDOLON_CORE_BINARY=/work/eidolon-core node scripts/accept-proxy.mjs
```

### 真实三进程结果

本轮新生产 standalone 构建复制进 Python 测试容器，Next、Go、Uvicorn/FastAPI 是三个独立真实进程，SQLite 和迁移前备份均位于测试临时目录。仅外部模型/OAuth/语音等为模拟实现。

| 配置 | Next / Go / Uvicorn PID | 首个非空文本 delta | 断开到模型实际 CancelledError |
|---|---|---:|---:|
| 无素材域名 | 59982 / 59973 / 59958 | 50.862 ms | 7.995 ms |
| 测试素材域名 | 60029 / 60020 / 60005 | 48.646 ms | 8.973 ms |

同一用户迁移历史会话读取/续聊、新建 SC 会话、正常 SSE done、中断后上游模型取消均通过；模拟 GitHub OAuth 302、多条 Set-Cookie、HttpOnly、Cookie/Origin/X-Work-ID 传递、恶意 Origin 与非 SC 写请求拒绝保持。跨作品和跨用户会话 ID 的 GET/DELETE/POST 都为 404，不泄露历史。等待模型进入无限等待后才断开，取消时间记录先于进程清理，2 秒窗口未扩大。实际 Uvicorn ASGI 2.3；第二阶段记录的 ASGI 2.4 升级风险未被本轮数据库改动解决。

Go 静态目录、29 角色全部 23 个模型字段、19 礼物、498 日期边界、1431 RAG 输入和 Prompt 等价重新通过；停止 Python 后完整 Go 目录仍能查询。测试自行停止子进程并清理其临时数据库/快照目录；失败日志保留。其余 pytest 临时产物保留在容器，不擅自全局清理。

## 资源最终记录

本轮复用三个已有容器；结束时全部为 exited，保留容器、缓存和日志。项目挂载只读，测试在容器副本中运行；不挂载 HOME、凭据、系统目录或 Docker socket，不发布宿主机端口。前端构建临时连接 bridge 获取公开构建资源，结束已断开。

| 容器 | 开始 SizeRw（字节） | 结束 SizeRw（字节） | 增量（字节） |
|---|---:|---:|---:|
| eidolon-accept-p2-go-20261024 | 419110912 | 520212480 | 101101568 |
| eidolon-accept-python-20261024 | 3039924224 | 3056115712 | 16191488 |
| eidolon-accept-node-20261024 | 1166495744 | 1171599360 | 5103616 |

三个复用容器可写层合计增加 **122396672 字节（116.73 MiB）**，包括 Go 编译/测试缓存、前端构建缓存、Python 测试数据库和日志。这是逻辑占用，不等于 OrbStack 稀疏磁盘实际增量；容器不是独占资源，不能把所有可写层增长都精确归因到单条测试命令。

本轮没有创建镜像、容器、卷、网络，也未安装依赖。最终系统总览为 8 镜像/4.832GB、6 容器/约5.01GB、1 卷/196.3MB、16 构建缓存项/126.8MB。总览含其他已有活动（如 eidolon-age-tools:local），不是本轮新建资源清单，不据此声称全局镜像/缓存数量未变。

宿主机额外产生源码、测试 SQL 夹具、文档及 `.scratch/db-scope-20261025/` 文本日志/审计文件，scratch 为 **392 KiB**；本轮没有在宿主机生成数据库、node_modules、虚拟环境或二进制构建产物。之前各轮 scratch、二进制、未跟踪文件和 `.DS_Store` 都保留。

未来清理需用户确认：本轮 scratch 可单独归档后删除；三个容器保存了多个阶段的证据，若不再需要，应先归档再逐个 `docker rm <明确容器名>`，会删除其整个可写层。共享镜像、卷和构建缓存不需要为本轮删除，不使用任何全局 prune。本轮只停止容器，没有执行上述删除。

## 2026-10-26 独立 Git 审查缺陷修复与最终复验

原验收结果全部保留。本节对应 Git 审查发现的 P2、两项 P3 及提交准备，不是生产迁移记录。

### 缺陷及修复

- **P2（已复现、已修复）**：合法旧库含 `messages.id=-1`，迁移本身成功，但普通自动编号 INSERT 在 BEFORE 阶段的 `NEW.id=-1` 被误当作显式替换。修复前新回归 **1 failed、2 passed**，失败精确落在 `-1` 自动追加；证据 `logs/negative-id-red.log`。
- 增加 `message_insert_guard` 辅助表和 BEFORE 探针，在 AFTER INSERT 获得实际编号后拒绝冲突。显式正负编号 REPLACE 仍以 `RAISE(ABORT)` 回滚整条语句并恢复原消息，不跳过负数、不削弱父会话或用户/作品边界。无需开启递归触发器。OR IGNORE 和多行追加亦覆盖，表分类与启动校验同步更新，详见逐表审计补充。
- **P3**：三进程循环拒绝断言附 `(body, headers)`；反射签名断言替换为实际无作用域 SQL 写入拒绝，并通过 SC/Demo 仓储验证无残留会话。
- 补充发现内容等价测试跨真实分钟会误报提示词不等；受控时钟实验仅“现在是”行不同。三个测试客户端统一 FakeClock，完整提示词比较未删减。此测试修正在第二阶段提交；失败、诊断和修正记录见第二阶段收尾报告。

### 最终证据

所有本轮日志位于 `.scratch/git-final-20261026/`，不进入提交。测试和构建均在既有 Docker 容器中进行，使用模拟凭据与临时数据库。

| 项目 | 实际结果 | 日志 |
|---|---|---|
| 最终 Python 全量 | **2081 passed、1 skipped、121 warnings**，63.78 秒；仅 Windows proactor 跳过 | `logs/stage3-verified.log` |
| ruff / pyright | 通过；0 errors、0 warnings | 同上 |
| 存储专项及三进程首次修复复验 | **26 passed、2 warnings** | `logs/storage-three-process.log` |
| 最终存储、三进程、Go↔FastAPI、全部内容契约 | **47 passed、2 warnings**，14.29 秒 | `logs/integration-fixed-final.log` |
| 第二阶段独立版本全量 | **2057 passed、1 skipped、121 warnings**；ruff/pyright 通过 | `logs/stage2-verified.log` |
| Go 单元/TCP、race、vet、构建 | 全部通过 | `logs/go-final.log` |
| 前端全量与类型检查 | **1158 passed**，类型检查通过 | `logs/frontend-final.log` |
| Next 生产构建 | 临时连接普通 bridge 下载真实公开字体后通过，随后恢复断网 | `logs/frontend-build-network.log` |
| 新构建代理矩阵 | **58 passed、0 failed**，3 轮，2000ms 观察窗口 | `logs/proxy-final.log` |

最终真实三进程两组首段延迟 **50.405 / 50.432 ms**，LLM 实际取消延迟 **7.347 / 7.574 ms**。没有放宽取消观察窗口。旧 SC 历史读取/续聊、跨用户/跨作品拒绝、OAuth 302、多 Cookie、Origin、X-Work-ID 与完整目录等价由该链路覆盖。

保留额外失败：`integration-final.log` 的时钟边界误报；`integration-clock-fixed.log` 的重开客户端未统一时钟导致会话过期，修正后重跑通过；`frontend-final.log` 和 `frontend-build-retry.log` 的断网字体失败；`go-restart-results/` 的旧脚本遗漏 go.sum 失败。未将这些运行描述为通过，原历史日志未改写；旧一次性 Go 容器内固定 `/results` 被其启动脚本更新，宿主机历次记录保持原样。

### 提交边界与限制

第二阶段合并三条静态内容切片；第三阶段单独提交 SQLite 作品隔离、离线迁移及以上修复。中间版本恢复基点旧存储代码，三进程 helper/test 去掉数据库迁移和隔离场景，ADR 去掉第三阶段补充；不是只按文件名拆分。两个版本分别完整测试，不把最终版本结果冒充中间提交结果。Go main.go 全部属于第二阶段。

复核未发现新的 P1/P2。没有访问真实数据库、真实 .env、外部模型或生产 OAuth。旧实验版 v1 缺少新增探针会被启动校验拒绝，需使用匹配旧备份重新执行离线迁移；未提供启动自动修补或生产升级捷径。备份及停写演练仍须由部署者针对实际环境另行执行。

本轮未安装宿主机依赖，未创建 Docker 镜像、容器或数据卷。复用既有容器，保留测试资源与所有 scratch。新增第二阶段容器源码副本约 **15.2 MiB**；宿主机 `.scratch/git-final-20261026/` 在提交准备前约 **10.7 MiB**，包含日志、阶段文件副本和跨容器传递的 Go 测试二进制，后续暂存核对材料会增加少量占用；这些均排除提交。构建缓存及 pytest 临时目录另有增量，未测得精确 Docker 总增量，不将文件大小等同于 OrbStack 磁盘增长。需要清理时可在审查结束后只删除本轮明确列出的 scratch/容器副本，本轮不执行清理。
