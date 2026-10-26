# 第二阶段首条切片：Go 世界内容快照

2026-10-25，基于 `refactor/multi-work-dlc` 的 `5cec1ad4`。本次完成可实际启用的世界内容迁移，**未完成第二阶段全部角色加载目标**。原第一阶段验收记录保留。未修改 SQLite schema、数据标识、生产配置或前端代码，未提交、推送或部署。

## 原链路与本次边界

原来 Python assembly 同时读取 characters.toml、角色人设/事实/素材、gifts.toml 和 world.json，再校验角色属于已知偶像或职员。Go 从 work.json 注册作品；SC 公开目录由 Python `/api/characters` 适配。

| 内容 | 原有语义及依赖 | 本次归属 |
|---|---|---|
| work_id、local character ID | 新目录用作品作用域 ID；旧 API/历史记录保持裸 SC ID | 注册与拒绝规则不变 |
| 名称、简介、生日、颜色、语音、礼物 | 角色 TOML 校验；礼物须属于目录；裸 ID 关联会话、记忆和好感度 | Python 保留 |
| 人设、事实、读音、开场白、头像、立绘 | 文本/文件读取、素材存在性、idol_id 顺序和 URL 派生；不可当作匿名公开快照 | Python 保留 |
| staff、units、idols、releases、songs、lives | 保留原顺序、nullable 字段、来源、CV、日期、演出 setlist | Go SC 适配器解析/规范化 |
| unit_of、songs_of、lives_of、prompt、entries | SC 专属组合/演出归属；按日期标记未来；RAG 不注入 CV | Python World 运行时保留 |
| 原作剧情、work_facts、retrieval | 原作导入及检索计算依赖领域语义 | 不迁移 |

选择世界内容是因为它有完整静态输入及独立的行为等价边界。角色 TOML 同时涉及资源派生、语音和公开目录，不能仅为迁移数量把规则翻译到 Go。本次不是让 Go 再向 Python 请求 world.json：离线导出完全不需要 Python 服务。

## 新链路与契约

`work.json 注册 + SC world.json → Go scworld → content.Snapshot → 私有文件 → Python World → 原 Prompt/RAG/目录逻辑`。

通用模型只有 `schema_version: 1` 和 `worlds`。每项包括稳定 `work_id`、明确 `format` 和对应 `data`，不要求其他作品具有 283 事务所、组合或演出结构。SC 格式是 `sc-world-v1`；Demo/普通包是 `text-world-v1`，data 为 `{setting: ...}`。作品按 ID 排序，域内数组顺序不变；未知格式不能冒充 SC。未来结构化世界需增加自己的适配器及格式契约，而不修改通用封装。

Go 对 SC 输入做递归类型、必需字段、UTF-8 和 ISO 日历日期校验，单源上限 16 MiB。原可选 releases/songs/lives/setlist 规范化为空数组；未知字段像旧 World.from_json 一样忽略。对无效类型和非规范日期比旧 Python 更严格；未调整有效现有内容。实体引用与重复名称未增加新的语义限制，仍保留旧运行时行为。

所有作品成功后才发布：输出同目录创建 0600 临时文件，写入并 sync，关闭后 rename 替换。失败保留旧文件；没有热更新或进程内半更新。此机制保证进程层面完整替换，不承诺断电时目录项持久性；没有目录 fsync。Python 有 32 MiB 读取上限，检查 envelope 版本、作品唯一性和显式 SC 格式，再物化原模型。配置后文件缺失、损坏、版本错误或缺少 SC 都使启动失败，且发生在数据库创建之前。

该文件含内部资料，包括 CV；没有新增 HTTP endpoint。不要放在 public、静态资源目录、公开对象存储或日志中。Go 现有 SC world API 仍返回 501。导出器与 Python 要使用一致的文件权限/UID，Python 仅需只读访问。

## 启用、更新与回滚

以下命令在已准备代码与二进制的隔离 Linux 容器中执行，**不是在 Mac 宿主机安装或构建**。测试容器已有 `/work/eidolon-core`、`/work/characters` 和 `/work/core`：

```sh
/work/eidolon-core -packs /work/core/packs,/work/core/testdata/packs \
  -sc-world /work/characters/world.json -export-worlds /work/worlds.json
```

启动 Python 时设置 `EIDOLON_WORLD_SNAPSHOT=/work/worlds.json`；真实应用的其他配置保持其原有提供方式。此次没有写入任何真实 .env，也未启动生产服务。正式使用时不要载入 Demo 测试包；让快照生成步骤成功后再启动消费者。world sync 或人工修改原文件后须重新导出，并与角色配置一同发布、重启 Python。快照不会自动随源文件更新，也没有源版本摘要或跨文件事务；不能混用不同内容版本。

回滚：取消该变量并重启 Python，恢复原 world.json 路径。数据库无回滚步骤。Go HTTP 代理是否启用与这个离线导出独立；导出进程退出后 Python 不依赖其存活，启动时无需网络 RPC。

## Docker 验收（2026-10-25）

证据保存于 `.scratch/content-world-20261025/`，不应提交日志或二进制。基线 2035 项 Python 测试，本次新增 11 项。

| 检查 | 实际结果 | 日志 |
|---|---|---|
| Go 单元及真实 TCP/HTTP | 通过，`EIDOLON_TCP_TESTS=1 go test -count=1 -v ./...` | `go/go-test.log` |
| Go race | 通过，TCP 开启、count=1 | `go/go-race.log` |
| Go vet / Linux amd64 build | 通过；gofmt 与容器源码一致 | `go/go-vet.log`、`go/go-build.log` |
| 全 SC 内容契约 | 11 passed；29 角色 × 498 日期边界，1431 RAG entries | `python/contract-final.log` |
| Python 全量 pytest，4 workers | **2046 passed、1 skipped**，121 warnings，65.03 秒 | `python/pytest-full.log` |
| Python ruff / pyright | 通过；0 类型错误 | `python/ruff-final.log`、`python/pyright-final.log` |
| Go ↔ FastAPI 独立复验 | 1 passed，真实 HTTP/临时 SQLite、模型与 OAuth 为 fake | `python/crosslanguage.log` |
| 前端完整测试 | **1158 passed / 77 files** | `node/frontend-tests.log` |
| 前端类型检查 / 生产构建 | 通过，正常下载字体、无 mock；保留 LINE Seed JP fallback warning | `node/typecheck.log`、`node/build-final.log` |
| Next 生产代理矩阵 | **58 passed、0 failed**，每取消场景 3 轮 | `node/proxy.log` |

唯一 pytest skip 为 Windows proactor 专用用例；Linux 不适用。弃用 warnings 属已有依赖。首次 pyright 命令未将 venv 放入 PATH，产生 444 个依赖解析错误；使用已有 venv 后通过，未通过改业务代码绕过，原日志 `python/pyright-missing-venv.log` 保留。首次前端构建时容器没有外网，Google Fonts 下载失败；临时连接 bridge 后成功，测试前已断开。原失败日志 `node/build.log` 保留。

契约测试直接调用本轮 Go 二进制导出真实 SC 输入，比较整个 World（含全部嵌套字段、顺序、CV、来源与 null），全量 RAG entries、每角色歌曲/演出/组合及所有发行/演出/as_of 日期前后一天的 prompt。API 测试对比全部公开目录字段、所有角色真实文字回合 System Prompt，并用新加载路径重新打开旧测试数据库读取所有角色会话。通过禁止调用 assembly.load_world 证明未回退原始加载。既有角色 TOML 仍共用同一加载器，因此本次不宣称证明了 Go 角色解析等价。

安全/兼容回归覆盖原 SC API、OAuth 模拟流程、Cookie、Origin、X-Work-ID、非 SC 拒绝、历史读取及 SSE。Go/FastAPI 测试与 Next→Go→HTTP 测试按边界组合验证；**没有新增一条同时启动 Next、Go、真实 FastAPI 的三进程端到端验收**。没有访问真实用户数据库、LLM、OAuth 提供者或生产服务器。

SSE 首段：Next→HTTP GET 1.872ms、POST 0.820ms；Next→Go→HTTP GET 0.726ms、POST 0.667ms。48 次取消延迟为 **0.151–4.155ms**，观察的是上游响应实际 close，覆盖响应头前/首段后、GET/POST、FIN/reset，沿用 2000ms 失败窗口，未扩大窗口。正常完成、上游异常、未结束上传、状态码和多条 Set-Cookie 同样通过；具体每次取消延迟见 `node/proxy.log`。预期故障场景的 socket error 输出不代表验收失败。

## 限制与后续

导出时 Go 同时持有源字节、解析对象及规范化输出，Python 启动时同时持有 JSON 和 World；尚未测峰值 RSS，输入上限不是内存预算保证。没有新增常驻进程，现有 Go 代理进程模型不变。快照读取只在启动执行，不会每请求增加解析开销。本地容器结果不是生产并发/性能验收。

角色 TOML/人设/素材及 Go 独立 SC 角色目录仍未迁移。下一条内容切片应复用本次完整目录与 Prompt 基线，再迁移角色静态字段，避免长期维持两套解释器。数据库阶段应先设计 work_id 与角色复合身份、跨角色记忆的作品边界、导入导出/删除/检索/备份迁移及兼容映射；迁移测试证明隔离后，才开放真实第二作品文字聊天。此次未开始这些工作。

修改范围：Go CLI、content 私有封装、SC world 适配器、catalog 导出和对应测试；Python config/assembly/快照消费者及契约测试；core README、本报告和 ADR-0029 补充。没有改 Go HTTP 代理、Next 路由、原世界运行时算法或数据库代码。

## 资源与交付清单

复用 `eidolon-accept-p2-go-20261024`、`eidolon-accept-python-20261024`、`eidolon-accept-node-20261024`，现均已停止并保留。没有安装依赖，没有新增镜像、容器、网络、命名卷或 Docker build cache 条目。Go 沿用无网络模式，Python 沿用内部网络；Node 仅生产构建下载字体时连接 bridge，之后断开。未新增挂载、宿主机端口或提权设置。

`docker system df` 的容器逻辑占用从本轮开始的 **4.607GB** 到结束的 **4.709GB**，约净增 **102MB**；该输出经过舍入，不能据此声称精确到 byte。未在本轮开头记录各容器 SizeRw，所以不以历史单容器值冒充本轮前测。最终 SizeRw：Go 387,575,808 bytes；Python 2,903,572,480 bytes；Node 1,155,739,648 bytes。镜像仍为 7 个/4.808GB，卷 1 个/196.3MB，build cache 12 条/103.1MB。证据见 `docker-resources.txt`。未测 OrbStack 稀疏磁盘在 macOS 上的物理分配增量。

宿主机除以下源码/测试/文档外，新增 `.scratch/content-world-20261025/` 约 **10MiB**（du 为 10,236KiB），包含日志和容器间传递的 Linux Go 二进制。没有在宿主机执行语言构建/测试或安装依赖。此前 scratch、`.DS_Store` 等未跟踪文件保留。未暂存任何文件。

待审查的 13 个文件：

- `backend/src/eidolon/assembly.py`
- `backend/src/eidolon/config.py`
- `backend/src/eidolon/content_snapshot.py`（新增）
- `backend/tests/test_content_snapshot.py`（新增）
- `core/cmd/eidolon-core/main.go`
- `core/internal/content/snapshot.go`（新增）
- `core/internal/scworld/load.go`（新增）
- `core/internal/scworld/load_test.go`（新增）
- `core/internal/catalog/snapshot.go`（新增）
- `core/internal/catalog/snapshot_test.go`（新增）
- `core/README.md`
- `docs/adr/0029-go-work-catalog-before-runtime-migration.md`
- `docs/migration/go-content-world-slice.md`（本报告）

没有执行清理。若用户之后决定弃用验收容器，可分别 `docker rm eidolon-accept-p2-go-20261024`、`docker rm eidolon-accept-python-20261024`、`docker rm eidolon-accept-node-20261024`；这会连同之前各阶段的依赖、缓存和临时数据库一起删除，应先保留需要的材料。scratch 是否删除由用户决定，不建议全局 prune。

结论：**世界内容切片通过本地隔离工程验收，可交审查；第二阶段完整角色内容迁移尚未完成。** 不将目录能力等同于安全的多作品聊天；本次到此停止。
