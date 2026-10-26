# 第二阶段第二条切片：Go 角色静态内容加载

2026-10-25；分支 `refactor/multi-work-dlc`，HEAD 仍为 `5cec1ad4`。上一条世界切片尚未提交，本次在其工作区改动上继续，未覆盖原改动或改写原验收结果。所有编译、依赖获取及测试在 Mac 上的 OrbStack Linux 容器中完成。

## 依赖审计与实际迁移范围

原链路：assembly → Python load_characters → characters.toml、readings.toml、人设/事实和素材检查 → Character；世界单独加载；app 派生完整公开目录，engine/retrieval/call 使用这些模型。

新链路：Go SC 适配器读取配置及关联文件 → 统一角色/世界快照原子发布 → Python 校验快照与本地输入摘要 → 共用 Character 物化逻辑 → 原业务引擎。旧加载路径仍可显式回滚。

| 分类 | 实际输入与规则 | Go 接管 | Python 保留 |
|---|---|---|---|
| 基础静态字段 | id/name/intro/color/birthday；name_zh/kana/romaji | TOML 解析、类型/身份/颜色/日历校验、颜色小写、其他名称 trim；文件顺序 | 防御性字段校验、Birthday 对象；旧公开目录 idol_id 排序 |
| 作品与组织 | SC 显式归属、世界中的偶像/职员名称 | 与同批世界关联校验；work_id 隔离 | unit_of 等 SC 运行时投影，不强制给其他作品同样组织结构 |
| 内部文本 | sheet、facts | 安全读取、UTF-8 校验、原文嵌入私有 documents | 草稿标记移除、Markdown facts 分段、Prompt/RAG 组装 |
| 语音与招呼 | voice_id、speakers、greeting/call_opener 及字幕；readings.toml | 配置解析，配对、非空、speaker 跨角色冲突、读音格式校验，保留顺序 | 语气提示、字典匹配、ScriptedLine、音频文件哈希与 URL、合成服务 |
| 图片/立绘/照片 | avatar/background/scsp_avatar；idol_id/default_outfit；photo_tag/appearance | 路径安全、文件状态及摘要、配置格式 | Character 中的 Path、实际文件响应、对象存储地址、照片业务 |
| 礼物 | liked_gifts、gifts.toml | 喜欢列表及静态引用、数量/重复/非全稀有校验；输入摘要 | 完整礼物目录解析与规则（分类、在售稀有度等）、背包和好感度 |
| 动态业务 | 用户信息、记忆、历史、好感度、剧情进度 | 无 | 全部保留，绝不进入共享快照 |

全角色的 23 个 Character 字段都参与等价比较，包括未在公开目录出现的人设、事实、语音和礼物。Go 不复制 facts 分段、语气过滤、日期 Prompt 或音频地址算法；Python 仍用一个物化实现供新旧来源共用。保留静态字段防御性校验是消费者的输入防线，不是第二个 TOML 来源。

新路径不解析 characters/readings TOML，也不由旧加载器读取世界/角色文本；为验证版本，它仍会读取这些文件的字节做摘要。不是可以丢掉整个 characters 目录的自足部署包。管理/素材生成 CLI 的旧源文件工作流未迁移；更新源文件后必须重新导出。

## 快照版本、一致性与路径安全

原 `-sc-world … -export-worlds …` 继续输出 v1；原 `EIDOLON_WORLD_SNAPSHOT` 保持兼容，也能读取 v2 的世界。新增 `-sc-characters …` 才输出 v2，世界必须是该配置目录下的 world.json。

v2 外层为 `schema_version: 2`、`payload` 和 `sha256`。payload 是 JSON 字符串，摘要计算其 UTF-8 字节；避免 Go/Python 重编码 JSON 时的转义差异。payload 内 `worlds` 和 `characters` 一起发布，每个角色集合都有 work_id/format/data。SC 格式 `sc-characters-v1` 包含 entries、documents、readings、config 文件名及 manifest；普通包为 `text-characters-v1`。通用层不含 SC 必填组织/礼物结构。

manifest 记录 characters.toml、world.json、gifts.toml、readings.toml 和所有引用的文本/图片的 SHA-256；允许缺失的资源用 null 记录“当时不存在”，后续突然出现也算版本变化。Go 发布前再次读取清单检查变化；Python 在打开数据库前验证文件摘要和嵌入文档原文。角色/世界一份原子文件，若同时配置两个环境变量，必须指向 payload 摘要相同的 v2 快照；与 v1 或不同批次混用则启动失败。摘要用于错误/版本检测，不是数字签名，不抵抗有权同时改写内容和摘要的人。

沿用 0600 同目录临时文件、sync、close、rename 发布；任何加载/写入失败都不替换原文件。不做热更新，亦不保证断电目录项持久性（无目录 fsync）。单文件输入上限 16MiB，累计读取预算 64MiB，最终快照上限 32MiB。

Go 以 os.Root 打开内容目录，禁止绝对路径、`..`、反斜杠、隐藏路径及符号链接越界；允许缺失的可选文件也经过同样约束。Python 对清单、条目和配置自身再次检查目录归属。上线时应只读挂载内容树；这些摘要检查不构成对可恶意并发修改文件系统的锁或热重载协议。

安全收紧仅影响危险/无效配置。现有缺少可选图片/事实时降级、缺少必需人设时失败、CRLF 文本读取、null/default 及顺序均保留。某些保留在 Python 的语义校验（例如去掉语气提示后完全没有词的招呼、完整礼物目录规则）仍可能使 Python 启动失败；Go 导出成功不替代消费者启动验证，也不宣称全部 Python 语义已迁往 Go。

## Go 原生目录与剩余反向依赖

Go 启动时传 `-content-snapshot <v2文件>` 后新增 `GET /api/works/{work_id}/static-characters`。仅白名单公开 id、name、intro、color、birthday 及三种其他名称（缺省字段省略），外层保留 work_id/local_id/全局 id；按源配置顺序。不公开 sheet、facts、voice_id、speakers、读音、喜欢礼物、照片提示词、路径或 manifest。它是基础静态目录，不是旧完整 JSON 的替身。

测试让 Python 端口不可用，仍成功读取 29 位 SC 角色和 Demo 的 `mano`：`sc:mano` 与 `demo:mano` 不覆盖。未知作品/子路由、重复身份、非法作用域及写操作仍拒绝。

**原 `/api/characters` 与 `/api/works/sc/characters` 完整展示目录仍依赖 Python。** 原目录没有用户动态状态，但有 idol 排序、组合、招呼/开场白音频哈希、立绘、poster/voxel 地址等派生语义。本次不在 Go 重复实现，也不静默删字段；旧界面无需变更。`http.go` 仅增加静态目录分支，SSE/HTTP 代理实现未调整。

## 容器内使用与回滚

以下示例供已构建二进制及准备内容目录的隔离 Linux 容器运行，不在 Mac 宿主机安装依赖或构建：

```sh
/work/eidolon-core -packs /work/core/packs,/work/core/testdata/packs \
  -sc-world /work/characters/world.json \
  -sc-characters /work/characters/characters.toml \
  -export-worlds /work/content.json
```

Python 设置 `EIDOLON_CHARACTER_SNAPSHOT=/work/content.json`，使用同版本 characters 目录；无需再设世界变量，若保留则指向同一内容快照。Go 服务使用 `-content-snapshot /work/content.json`，其 `-packs` 必须与快照作品集合一致。正式内容配置不应包括 Demo 测试包。文件保持私有可读权限，不能放进 public 或公开对象存储。

回滚时取消角色快照变量并重启 Python；如回到世界 v1 切片，可恢复原世界变量。不需要数据库回滚。Go 去掉 content-snapshot 后仅停用新静态目录，旧目录/代理继续原行为。没有修改任何真实 .env、Compose 或生产配置。

## 测试与证据

日志根目录 `.scratch/content-characters-20261025/`；新测试只使用模拟凭据及临时 SQLite。私有字段比较只报告角色 ID/字段名，不打印完整内部文本。

| 检查 | 结果 | 日志 |
|---|---|---|
| Go 单元 + 真实 TCP/HTTP | 通过，`EIDOLON_TCP_TESTS=1 go test -count=1 -v ./...` | `go/unit-tcp-final.log` |
| Go race / vet / gofmt | 通过，TCP 开启，格式输出为空 | `go/race-final.log`、`go/vet-final.log`、`go/gofmt-final.log` |
| Linux amd64、CGO=0 编译 | 通过 | `go/build-amd64.log` |
| 世界 + 角色契约首轮 | 17 passed：旧世界 11 项、新角色 6 项 | `python/contract.log` |
| 路径补强后角色契约 | 6 passed；全部 29 角色 × 23 字段 | `python/contract-final.log` |
| Python 首轮全量 | 2052 passed、1 skipped、121 warnings，63.80 秒 | `python/full.log` |
| 路径补强后 Python 最终全量 | **2052 passed、1 skipped、121 warnings，61.77 秒** | `python/full-final.log` |
| Python ruff / pyright | 通过 | `python/ruff-final.log`、`pyright-final.log` |
| Go → FastAPI 独立复验 | 1 passed，0.95 秒 | `python/crosslanguage.log` |
| 前端完整测试 | 1158 passed / 77 files | `node/tests.log` |
| TypeScript / Next 生产构建 | 通过，真实字体获取；既有 LINE Seed JP fallback warning | `node/typecheck.log`、`build.log` |
| Next 生产代理矩阵 | 58 passed、0 failed | `node/proxy.log` |

角色测试同时覆盖：所有字段/事实/读音等价、旧加载器禁用、资源 URL 开/关两种配置、全部角色真实文字回合 System Prompt、图片实际响应、旧会话消息读取与续聊、可选资源缺失、CRLF、快照损坏/版本/摘要/作用域/重复身份、来源变化、新资源出现、目录外符号链接和失败保留旧快照。Go 单元补充 TOML、字段、生日、角色世界/礼物引用、路径/符号链接、读音及招呼配对等拒绝分支。

Go v2 封装另测摘要损坏、截断、版本错误、重复作用域和写入失败后原文件不变，见最终 Go 日志。唯一 pytest skip 是 Windows proactor 专用测试（Linux 不适用）；warnings 为现有依赖弃用提示。本轮没有用修改断言掩盖测试失败，初轮和收尾复跑均通过，日志分别保留。

全量 suite 包含原 SC 文字历史、OAuth 模拟、Cookie/Origin/作用域拒绝、礼物/语音等回归；Go/FastAPI 为真实 TCP 与临时 SQLite，外部 OAuth/LLM 使用 fake。Next 生产矩阵的上游是 HTTP 测试服务，不把它描述成同时运行 Next、Go、真实 FastAPI 的三进程验收。生产环境、真实 OAuth/LLM、并发峰值内存及容器镜像构建本轮未验证；已验证 Linux 二进制编译，Dockerfile 同步加入 go.sum。

SSE 首段：Next→HTTP GET 0.879ms / POST 0.660ms；Next→Go→HTTP GET 0.605ms / POST 0.610ms。48 次取消传播 **0.137–3.662ms**，观察上游实际 close，覆盖头前/首段后、GET/POST、FIN/reset；失败窗口沿用 2000ms。正常完成、异常上游、流式上传、状态码、OAuth 302、多条 Cookie 与 Origin/X-Work-ID 均通过。

## 后续边界与结论

本次引入小型 TOML 解析依赖 `github.com/pelletier/go-toml/v2 v2.2.4`（MIT），go.mod/go.sum 锁定；没有手写 TOML 解析器或引入 Schema 框架。Go 适配器和 Python 物化逻辑通过真实内容契约约束。大快照在 Go 导出及 Python 启动时有原文/JSON/模型的暂时多份内存，未测峰值 RSS；来源摘要增加启动 I/O，不增加每回合解析。

为未来 Gakumas 添加基础静态角色可沿用 Demo 的 work.json 格式，work_id 改为 `gakumas`、source 为 `content`，characters 中仍为 id/name/intro/sheet；同名 local ID 不冲突。通用快照将其导出为 text-characters-v1。若需 SC 之外的资源/语音/事实模型，应增加该作品的版本化适配器和消费契约，而不是填造 SC 偶像编号或礼物规则。此说明不是正式学偶接入，当前任何非 SC 运行时仍明确拒绝。

数据库阶段的前置条件仍是 work_id/角色复合身份、跨角色共享记忆限定同作品、查询/检索/删除/导入导出/备份的作用域迁移和兼容裸 SC ID 测试；完成前不得开放第二作品聊天。此次没有开始数据库、OAuth、对话事务、记忆/好感度或 WebRTC 迁移。

已实现并验证的是 Go 真实角色静态加载 → 统一私有快照 → Python 原业务模型，以及独立 Go 基础静态目录。**不能宣布第二阶段全部内容模型迁移完成**：完整 Go SC 展示目录去依赖、完整礼物配置和管理/素材 CLI 内容路径仍留待后续，保留的运行时语义也未迁移。没有留一个“实现但未测”的角色消费开关；未验证范围是生产和上文明确列出的部署/性能事项。

## 实际文件清单

以下为本次角色切片触及的 20 个源码、测试及文档文件；其中多项继承上一条尚未提交的世界切片，不能将当前全部 diff 误算为本次新增。上一条的世界测试和验收报告保留原样。

- Python：`backend/src/eidolon/assembly.py`、`characters.py`、`config.py`、`content_snapshot.py`，`backend/tests/test_character_snapshot.py`（新增）。
- Go 入口与依赖：`core/cmd/eidolon-core/main.go`、`core/go.mod`、`core/go.sum`（新增）、`core/Dockerfile`。
- Go 内容：`core/internal/content/snapshot.go`、`snapshot_test.go`（新增），`core/internal/sccharacters/load.go`、`load_test.go`（均新增），`core/internal/scworld/load.go`（提取已安全读取字节的验证入口）。
- Go 目录：`core/internal/catalog/catalog.go`、`http.go`、`snapshot.go`。
- 文档：`core/README.md`、`docs/adr/0029-go-work-catalog-before-runtime-migration.md`、本报告 `docs/migration/go-content-character-slice.md`。

前端源码、数据库 schema、角色内容源文件、生产配置与 .env 均未修改。宿主机与容器的 Go 源码以及最终 Python 消费者/测试文件已对照；git diff --check 通过，暂存区为空。分支与 HEAD 未改变，无提交、推送或 PR。

## Docker 和宿主机资源

复用既有三只验收容器，结束后均为 exited 并保留。新增镜像、容器、网络、命名卷和 Docker build cache 条目均为 0。Go 仅获取 TOML 依赖时临时连接 bridge，随后恢复 none；Python 沿用内部网络；Node 仅生产构建取字体时连接 bridge，代理测试前断开。没有发布宿主机端口、增设挂载或提权。

本轮启动前和停止后的 `docker inspect --size` 实测：

| 容器 | 前 SizeRw / bytes | 后 SizeRw / bytes | 净变化 / bytes |
|---|---:|---:|---:|
| eidolon-accept-p2-go-20261024 | 387,575,808 | 407,855,104 | +20,279,296 |
| eidolon-accept-python-20261024 | 2,903,572,480 | 2,930,421,760 | +26,849,280 |
| eidolon-accept-node-20261024 | 1,155,739,648 | 1,161,273,344 | +5,533,696 |
| 合计 | 4,446,887,936 | 4,499,550,208 | **+52,662,272（50.22MiB）** |

增量包含依赖/编译缓存、源码副本、日志及测试临时数据库等；是 Docker 逻辑大小，不是 OrbStack 稀疏盘的 macOS 物理分配量，后者未测。最终镜像仍为 7 个/4.808GB，容器 6 个（全部停止），卷 1 个/196.3MB，build cache 12 条/103.1MB。结束证据为 `docker-resources.txt`，前测数值来自本轮首次 Docker 检查。

宿主机新增 `.scratch/content-characters-20261025/` **10,572KiB，约 10.32MiB**，含容器日志和用于跨容器传递的 Linux Go 二进制；不应提交。另有上述源码/测试/文档修改及 go.sum。未在宿主机安装依赖或执行 Go/Python/Node 构建测试。此前 scratch 与 `docs/.DS_Store` 等未跟踪文件保留。

没有执行资源清理。用户决定弃用环境时，可分别 `docker rm eidolon-accept-p2-go-20261024`、`docker rm eidolon-accept-python-20261024`、`docker rm eidolon-accept-node-20261024`；这会删除这些容器各阶段累积的依赖、缓存和临时数据，先保存所需证据。scratch 是否保留同样由用户决定，不需要全局 prune。到此停止，不进入数据库迁移、真实学偶 DLC 或生产部署。
