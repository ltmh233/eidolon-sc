# 第二阶段第三条切片：统一内容目录与真实三进程验收

日期：2026-10-25。结论：在下述启用条件和边界内，第二阶段静态内容加载及完整 SC 目录去运行时依赖已完成；Go 对话引擎未迁移，生产未部署。

## 基线与范围

分支 `refactor/multi-work-dlc`，HEAD `5cec1ad4f3d8353653b73364326702d2c06af5e2`。世界与角色两个切片在本轮开始前已未提交；历史报告 `go-content-world-slice.md`、`go-content-character-slice.md` 保留原样。本轮不暂存、提交、推送、建 PR 或修改数据库 schema。已有 `.DS_Store`、scratch 与其他未跟踪文件保留。

累计 diff 包含三条切片，不能全部记为本轮新增：

| 归属 | 主要文件/职责 |
|---|---|
| 世界切片 | `core/internal/scworld/`、私有世界快照基础、`backend/src/eidolon/content_snapshot.py` 的 v1、`test_content_snapshot.py`、世界报告 |
| 角色切片 | `core/internal/sccharacters/` 的角色解析、`content/` 的 v2 摘要、`catalog/snapshot.go` 与测试基础、`characters.py` 物化、`test_character_snapshot.py` 基础、Go TOML 依赖与 Dockerfile、角色报告 |
| 本轮新增 | `character_catalog.py`、`content_publish.py`、`test_content_release.py`、`test_three_process.py`、`three_process_backend.py`、`catalog/display.go`、`sccharacters/gifts.go` 和 `gifts_test.go`、本报告 |
| 本轮续改已有文件 | `app.py` 抽取纯目录投影；`assembly.py`/`config.py` 接入礼物及展示校验；`gift_catalog.py` 共用规范化校验；`content_snapshot.py` 接受 v3；Go `cmd/eidolon-core/main.go`、`content/snapshot.go`、`sccharacters/load.go`、`catalog/{catalog,http,snapshot,snapshot_test}.go`；`test_character_snapshot.py` 增加 v3 与素材删除；README 和 ADR-0029 |

`scratch` 中的 `cumulative-at-start-of-verification.diff` 是验证阶段的累计差异，不是本轮开始前的干净基线。源码之外的二进制、诊断探针、日志均不建议提交。

## 完整 SC 展示目录审计与实现

原链路：Next → Go 适配 → Python `/api/characters` → Character/World + 部署素材基址 → 纯展示函数。此次将原函数和列表投影抽取为 `character_catalog.display_characters`，旧 API 直接调用同一实现。未改变排序、null、哈希、相对路径与 JSON 字段。

| 分类 | 字段/来源 | 本轮处理 |
|---|---|---|
| A 静态 | id、name、intro、color、birthday、idol_id、各语言名字 | Go 规范化角色，投影发布 |
| B 可离线派生 | idol 排序、unit/reading/order、招呼/开场白内容、音频哈希、poster/voxel 路径、立绘 outfit/list 路径 | 复用现有 Python 纯算法离线计算，不复制 TTS 或存储逻辑 |
| C 部署相关 | assets_url 决定立绘及音频等绝对 URL；头像/背景的相对 API URL | 发布时显式参数；Python 启动验证同一素材基址；本轮测试有/无基址 |
| D 动态 | 该完整目录 JSON 无必须访问用户状态或在线服务的字段 | 无运行时 Python 调用；素材文件服务、实际资源存在性与生成仍是既有职责 |
| E 禁止公开 | sheet、facts、Prompt、voice_id/内部语音参数、源路径、manifest、完整私有快照 | Go 嵌套类型白名单拒绝额外字段；测试覆盖顶层人设和嵌套 voice_id |

Go 配置 `-content-snapshot` 和 `-catalog-display` 后，原生返回 `/api/works/sc/characters`；每项仍有 `sc:mano` 作用域身份，`display` 等于旧完整目录。停止真实 Python 后仍可查询完整目录。未配置展示元数据时保留旧适配回滚路径。旧 `/api/characters` 和 `/api/characters/{id}/avatar` 等资源端点仍由 Python 提供，因此“目录去依赖”仅指新作用域目录 JSON，不等于整站脱离 Python。

## 礼物与发布契约

v3 包含世界、角色、完整礼物静态定义，仍为一次私有文件原子发布。Go 校验 ID、名称、分类、稀有度、retired、重复项和每种稀有度的活跃礼物；保持原顺序和名称字符串语义。Python 消费规范化数据，不再在 v3 模式读取四个原始静态加载器。19 项礼物与旧加载器完整相等；背包、赠送事务、好感度、权限、随机玩法及用户数据仍全部由 Python 负责。

- `-with-gifts` 显式生成 v3（SC 角色格式 `sc-characters-v2`）；不加该选项仍生成 v2。旧版本消费者不会将 v3 当成 v2。
- v1 世界模式、`EIDOLON_WORLD_SNAPSHOT`、v2 角色模式及未启用快照的旧路径保留。v2 的礼物继续走原始加载器。
- 新离线入口：`python -m eidolon.content_publish`，输入快照和角色配置，显式可选素材基址，输出公开展示元数据。参数及部署示例见 `core/README.md`。
- Go 校验展示元数据绑定的内容摘要与公开字段；Python 配置 `EIDOLON_CATALOG_DISPLAY` 后，在创建数据库前比较摘要、完整投影和部署配置。
- Go 启动还拒绝快照与已注册非 SC 包的世界/角色内容不一致，避免部分作品来自不同版本。
- 私有快照和展示发布器采用临时文件、fsync、原子替换，权限 0600。两个输出不是跨文件事务；必须发布到新的不可变版本目录，同批校验后重启双方，无热更新。
- 双方各自使用不同但完整合法的版本，单机校验无法自动发现：发布前需比对 Go/Python 完整目录并核对 Go `X-Content-ID`。Next 现有头部白名单不转发该摘要头，本轮未改变它。

验证覆盖摘要损坏、源文件变化、素材新增及删除、路径逃逸/链接、缺失输入、混用 v2/v3 展示、不同素材基址、部分作品失配及无效发布保留旧文件。中断测试在 `os.replace` 前注入 KeyboardInterrupt，证明替换前旧文件保留；没有做断电/文件系统崩溃恢复实验，也没有跨服务原子切换保证。回滚需同时选取旧消费者与同批 v2 源内容/快照，或撤销快照与展示配置；不需要数据库回滚。

## 管理工具边界

审计 `backend/src/eidolon/__main__.py`：剧情 import/sync、立绘 fetch/outfits、readings draft、greeting upload、sheet draft、reembed 与 eval 工具仍使用 Python 原始加载器；这是编辑/生成/维护流程，不是 v3 服务启动时重复解释配置。stand upload、greeting upload 等远程工具本轮未连接真实服务，也未改真实素材。

工作流：在独立源内容副本编辑或生成 → Go 校验导出 v3 → Python 发布展示并验证 → 将源内容/素材/两个输出作为不可变批次 → 同批启动两个消费者 → 对比目录 → 切换。新增或删除 manifest 覆盖的本地素材会使旧快照在下次启动时拒绝；正在运行的实例不会持续扫描源目录。对象存储中同名资源被覆盖不在本地 manifest 保证内，需版本化路径/发布纪律。旧 CLI 的全量离线测试继续运行，没有重写管理工具或执行真实上传。

## 本轮测试结果与证据

日志保存在 `.scratch/content-final-20261025/logs/`。所有语言命令均在复用的 Docker 容器内运行；没有宿主机依赖安装、编译或测试。

| 验证 | 实际结果 | 日志 |
|---|---|---|
| Go 单元及真实 TCP | 14 个顶层测试通过（含子场景），4 个测试包；命令包无测试 | `go/unit-tcp-final.log` |
| Go race | 4 个测试包通过 | `go/race-final.log` |
| Go vet / gofmt / Linux amd64 CGO=0 build | 全部成功；无诊断输出 | `go/vet-final.log`、`gofmt-final.log`、`build-amd64-final.log` |
| Python 全量（含真实三进程两个参数） | **2057 passed, 1 skipped, 121 warnings**，64.37 秒；跳过 Windows proactor 专属测试 | `python/pytest-full.log` |
| Python ruff | 通过 | `python/ruff-final.log` |
| Python pyright | 0 errors / 0 warnings | `python/pyright-final-explicit.log` |
| 内容/真实三进程/Go↔FastAPI 专项 | **23 passed, 2 warnings**，12.18 秒 | `python/contracts-e2e-final.log` |
| 前端全量 | **77 文件、1158 tests passed** | `node/tests.log` |
| TypeScript / Next 生产构建 | 成功 | `node/typecheck.log`、`build.log` |
| Next→Go→HTTP 上游生产代理矩阵 | **58 passed, 0 failed**；3 轮、2000ms 观察窗口 | `node/proxy-final.log` |

全量等价检查：29 角色 × 全部 23 个 Character 字段；19 礼物；498 日期边界；1431 RAG 输入；完整 System Prompt、旧历史 SQLite、两种素材基址与公开 JSON 排序。以上是本轮重新执行，不沿用上一轮结果。警告主要为现有依赖的弃用提示，未宣称零警告。

复现命令（容器项目目录，已准备依赖和生产 standalone）：

```sh
# /work/core
EIDOLON_TCP_TESTS=1 go test -v ./...
EIDOLON_TCP_TESTS=1 go test -race ./...
go vet ./...
gofmt -l cmd internal
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -o /results/eidolon-core-amd64 ./cmd/eidolon-core
# /work/backend
.venv/bin/ruff check .
.venv/bin/pyright --pythonpath /work/backend/.venv/bin/python
EIDOLON_CORE_BINARY=/work/eidolon-core EIDOLON_NEXT_SERVER=/work/next-standalone/server.js .venv/bin/pytest -n 4 -q -rs
EIDOLON_CORE_BINARY=/work/eidolon-core EIDOLON_NEXT_SERVER=/work/next-standalone/server.js .venv/bin/pytest -q -s tests/test_three_process.py tests/test_content_release.py tests/test_character_snapshot.py tests/test_content_snapshot.py tests/test_go_catalog.py
# /work/frontend
npm test
npm run typecheck
npm run build
EIDOLON_CORE_BINARY=/work/eidolon-core node scripts/accept-proxy.mjs
```

## 三进程与 SSE 的实际证据

在 Python 测试容器内分别启动真实生产 Next standalone、真实 Go 编译产物和 Uvicorn/FastAPI，四个 PID（含 pytest）不同；数据库为临时 SQLite。仅 OAuth、模型、语音、外部目录服务使用测试实现。真实请求经过三层，FastAPI 记录收到的 Origin、X-Work-ID 与自身 PID。

| 素材基址 | Next / Go / Uvicorn PID | 首个非空文本 delta | 客户端断开到模型 CancelledError |
|---|---|---|---|
| 无 | 15301 / 15293 / 15278 | 47.110 ms | 3.966 ms |
| `https://assets.example.invalid` | 15353 / 15339 / 15324 | 45.915 ms | 4.626 ms |

模型先输出真实格式片段，进入无限等待并写入 waiting 标记；客户端确认标记后断开。只在模型捕获 CancelledError 时记取消时间，断言发生在服务清理之前，不把进程退出当取消成功。2 秒延续既有本地验收窗口，用于发现持续挂起，远高于实测毫秒延迟；没有通过扩大窗口或定时主动结束模型变绿。正常流验证 done，取消流验证实际模型任务取消。当前 Uvicorn 实际声明 ASGI 2.3。

覆盖：SC 完整目录和停止 Python 后目录继续服务；sc:mano/demo:mano 隔离；未知作品/角色；真实认证会话、模拟 GitHub 302、多条 Set-Cookie/HttpOnly；同意条款、创建会话、文字 SSE、历史读取；Origin 与 X-Work-ID 到达 Python；非 SC header/body 写入 409、DLC 写入 405、恶意 Origin 403、未认证 401。测试清理自己创建的进程和临时目录/数据库；失败仅复制服务日志到 pytest 临时目录。测试依赖两个显式环境变量，缺失时跳过；不能将普通 CI 运行视为已经执行三进程验收。

## 失败记录与撤回的诊断改动

没有删除早期失败日志，也没有把未命中的故障注入记为验证成功：

1. `three-process-first.log`：测试误以为 Next 会转发自定义 X-Content-ID。修正为直连 Go 检查摘要、Next 比较相同完整 JSON；没有修改代理白名单。
2. `chain-release-second.log`、`disconnect-chain-after.log`：取消测试覆盖了 FakeLLM.stream，但实际适配器调用 stream_with，测试未进入预期等待。修正夹具入口并增加 waiting 标记。
3. `disconnect-red.log`：最小探针在 ASGI 2.4 下复现空闲流不能通过 receive 观察断开，2.3 通过。尝试运行时修复后，`three-process-mutation-red.log` 中撤回修复的全链路仍然通过——**该故障注入未命中 2.4 分支，不是成功的红灯证据**。实际 Uvicorn 声明 2.3；撤回不必要的 `_TurnResponse` 修改，最终源码保留原行为。探针移至 scratch，仅作为未来升级风险证据，不混入本轮通过统计。
4. `ruff-first.log` / `pyright-first.log` 及后续日志保留初期静态检查过程；`pyright-final.log` 的 452 个缺依赖错误来自未指定虚拟环境解释器，显式指定后 `pyright-final-explicit.log` 为零错误。没有通过关闭规则消除错误。

## 资源、边界与后续条件

资源最终数值及清理说明见下节。未安装新依赖或镜像；前端构建期间暂时连接默认 bridge，随后断开。测试不发布宿主机端口，不挂载 HOME、凭据、系统目录或 Docker socket；项目挂载只读，构建使用容器内副本。没有读取真实 `.env`，没有生产数据库、模型或云服务请求。

剩余边界：ASGI 2.4 升级风险需另立验证；本轮实际栈为 2.3。未验证真实 OAuth/LLM/TTS/CDN 可用性、生产负载和内存峰值、部署切换及断电恢复。内容摘要提供一致性而非签名认证，发布输入仍应可信。Next 自定义摘要头过滤保持原样。完整目录生成仍需要离线 Python，素材端点与全部用户运行时仍由 Python 服务。

可宣布：启用 v3 后静态规范化来源统一为 Go；启用同批展示元数据后作用域 SC 完整目录不依赖运行中的 Python。不可宣布：Go 聊天引擎、多作品数据库、DLC 对话或生产验收完成。

第三阶段的前置条件是先确认 `(work_id, local_id)` 身份、旧 SC 数据默认归属、同作品共享记忆规则，以及导入导出/删除/检索/事务/回滚方案，继续保持 Python 单写责任。此处仅记录前置条件，不实施迁移。建议审查后三条内容切片分别建立提交边界，第三条提交说明可为 `feat(content): publish unified gifts and native SC catalog with real gateway acceptance`；不要包含 scratch、二进制、日志或 `.DS_Store`。本轮未执行任何 Git 写入操作。

## 资源最终记录

复用并在测试结束后停止以下三个容器，均保留未删除。`docker inspect --size` 的可写层差异如下（这是 Docker 逻辑占用，不能等同 OrbStack 稀疏磁盘文件实际增量）：

| 容器 | 开始 SizeRw（字节） | 结束 SizeRw（字节） | 增量（字节） |
|---|---:|---:|---:|
| eidolon-accept-p2-go-20261024 | 407855104 | 419110912 | 11255808 |
| eidolon-accept-python-20261024 | 2930421760 | 3039924224 | 109502464 |
| eidolon-accept-node-20261024 | 1161273344 | 1166495744 | 5222400 |

总增量 **125980672 字节，约 120.14 MiB**，主要是构建产物、复制到 Python 容器的 Next standalone、测试缓存及日志。镜像仍 7 个 / 4.808GB，已有卷仍 1 个 / 196.3MB，构建缓存仍 12 条 / 103.1MB；新增镜像、容器、卷、网络均为 0。本轮没有安装新依赖。结束状态原始记录：`.scratch/content-final-20261025/docker-final.txt`。

宿主机新增源码/测试/报告，以及 `.scratch/content-final-20261025/` 的日志、诊断探针和一个测试 Go 二进制；该 scratch 目录 `du -sk` 为 **10812 KiB（约 10.56 MiB）**。没有在宿主机生成 node_modules、虚拟环境或编译缓存。测试在容器内使用项目副本；前端生产构建没有回写到宿主机。

本轮没有执行资源删除。未来经用户确认，可只删除本轮 scratch 目录；复用容器还保存前两轮数据，若确实不再需要，先归档所需证据，再逐个 `docker rm <上述明确容器名>`，这会删除其全部可写层，不能只视作本轮缓存清理。共享镜像/已有卷不需要为本轮删除；禁止使用全局 prune。当前容器已停止，不需要继续运行服务。

## 2026-10-26 Git 提交前独立版本复验

第二阶段三条内容切片合为一个完整提交。`main.go` 的全部新增内容属于静态内容导出/加载，无第三阶段数据库职责；三进程测试和 ADR 的 SQLite 增量留给下一提交。中间版本使用基点 `5cec1ad4` 的 Store、Database、记忆、成就、触达及对应旧测试，不包含 `work_schema` / `WorkConversations`，在独立 Docker 源码副本中验证。

本次发现内容等价测试使用真实时钟，旧/新后端跨分钟时，系统提示词仅“现在是”一行不同。受控时钟相差一分钟稳定失败，同一时钟通过；改为旧后端、快照后端、重开历史库客户端共用 `FakeClock`，仍完整比较提示词、素材和历史。最初遗漏重开客户端时钟导致会话过期，失败日志亦保留，最终三个客户端统一时钟。该改动仅限测试，没有改变运行时语义。

本轮证据目录为 `.scratch/git-final-20261026/`（不提交）：

- `logs/stage2-verified.log`：独立第二阶段 Python **2057 passed、1 skipped、121 warnings**，63.23 秒；Windows 专属测试跳过。ruff / pyright 同日志。
- `logs/stage2-integration.log`：真实三进程和全部内容契约 **23 passed**；该次在统一时钟前通过，统一时钟后由上述全量再次覆盖，未将第三阶段测试计入第二阶段。
- `logs/go-final.log`：Go 单元、TCP、race、vet 和构建通过；使用完整 `go.mod` / `go.sum`。
- `logs/frontend-final.log`：前端 **77 文件、1158 tests passed**，类型检查通过；其后的首次构建因字体下载失败，不能将此文件描述为构建通过。
- `logs/frontend-build-retry.log`：断网下重试仍失败。实际容器没有连接网络；临时连接普通 bridge 下载公开字体后，`logs/frontend-build-network.log` 生产构建通过，随后恢复断网。没有字体 mock、依赖降级或宿主机配置改动。
- `logs/proxy-final.log`：本轮生产构建及重新构建的 Go，代理矩阵 **58 passed、0 failed**，3 轮，2000ms 窗口。
- `logs/content-clock-red.log` / `content-clock-control.log`：受控时钟差异实验；原始偶发失败在 `logs/integration-final.log`，不覆盖旧记录。

首次误启动旧 Go 一次性容器，其脚本未复制 `go.sum`，造成依赖校验失败。输出另存 `go-restart-results/`；改用既有常驻 Go 容器后通过。旧一次性容器的固定 `/results` 输出被其启动脚本更新，但宿主机已有历次验收日志保持原样。本次不将失败运行计为通过。
