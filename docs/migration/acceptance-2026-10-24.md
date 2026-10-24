# 第一阶段 Go 多作品 DLC：Mac Docker 工程验收

后续记录：[SSE 断开传播修复与补充验收](acceptance-sse-2026-10-24.md)。以下保留首次验收时的结果，包括当时的 SSE 失败。

最终收尾：[Python 全量失败排查与复验](acceptance-python-2026-10-24.md)，记录两项测试时序修复、两轮完整回归及阶段验收结论。

最终代码审查后：[两项 P2 修复与复验](acceptance-p2-2026-10-24.md)，补齐文字回合 body 作用域拒绝与取消测试失败清理。

日期：2026-10-24（Asia/Shanghai）。分支：`refactor/multi-work-dlc`；验收开始时 HEAD 为 `201fe209`，重构前基点为 `3602e8d7`。保留开始时已有的未提交变更。本次未提交、推送、部署或执行第二阶段迁移。

**结论：Go、Python、前端自动化检查及 Go/FastAPI 联调通过；完整入口的 SSE 客户端断开传播仍失败，因此不能宣称全部工程验收通过。** 该失败在不经过 Go 的原 Next.js 入口同样复现，作为已有风险保留。本轮修复了新作品范围在 Next.js 入口丢失的问题，以及先前新增跨语言测试的类型错误。

## 环境与隔离

- 沙箱内访问 Docker Socket 被拒绝；用户授权申请沙箱外权限后，连接本机 OrbStack 的 Docker Engine 29.8.2 成功。容器为 Linux arm64。
- 使用 `golang:1.27`、`node:22-bookworm`、`ghcr.io/astral-sh/uv:python3.11-bookworm-slim`；实际 Go 1.27.3、Node 22.23.4、npm 10.9.9、Python 3.11.14、uv 0.9.30。
- 项目目录或其子目录仅以只读方式挂载到 `/source`，选定源码复制到容器 `/work` 后安装、编译及测试。复制时排除 `.env*`；不复制宿主机依赖、前端构建产物或用户数据库。
- Go：4 CPU / 2 GiB，`--network none`。Python、Node：各 4 CPU / 4 GiB。安装依赖和获取构建字体时使用普通 bridge 网络；未发布宿主机端口。
- Python 测试最终使用独立 `--internal` 网络，提供 WebRTC 所需的非回环接口但没有外网出口；Go/FastAPI 联调使用容器内回环 TCP。Node 代理验收关闭外网。
- 数据库来自 pytest 临时目录，身份、LLM、语音服务均使用既有 fake 和测试凭据。没有读取真实 `.env`、连接生产服务或访问真实用户数据。
- 没有宿主机依赖安装、构建、sudo、系统配置改动、Docker Socket 挂载、host 网络或 privileged 容器。

## 实际结果

完整日志位于仓库 `.scratch/dlc-acceptance-20261024/`，以下路径相对该目录。

| 项目 | 命令 / 场景 | 最终结果 | 日志 |
|---|---|---|---|
| Go 单元测试 | `go test -v ./...` | 通过；TCP 测试由下一项显式启用 | `go/unit.log` |
| Go race、覆盖率、TCP | `EIDOLON_TCP_TESTS=1 go test -race -cover -v ./...` | 通过；catalog 语句覆盖率 95.4%，CLI 无单元覆盖 | `go/race-tcp.log` |
| Go vet | `go vet ./...` | 通过 | `go/vet.log` |
| Go 编译 | `go build -o /work/eidolon-core ./cmd/eidolon-core` | 通过 | `go/build.log` |
| Python 锁定依赖安装 | `uv sync --locked` | 通过 | `python/install.log` |
| Python 全量回归 | `timeout 550 uv run --no-sync pytest -n auto -rs`，`PYTEST_XDIST_AUTO_NUM_WORKERS=4` | **2035 passed，1 skipped**；唯一跳过是 Windows proactor 专属测试 | `python/pytest-final.log` |
| ruff | `uv run --no-sync ruff check` | 通过 | `python/ruff-final.log` |
| pyright | `uv run --no-sync pyright` | 0 errors、0 warnings | `python/pyright-final.log` |
| Go ↔ FastAPI | `EIDOLON_CORE_BINARY=/work/eidolon-core`，单独及全量执行 `test_go_catalog.py` | 通过，未跳过 | `python/cross-language.log`、全量日志 |
| 前端锁定依赖安装 | `npm ci` | 通过 | `node/install.log` |
| 前端测试 | `npm test` | **77 files，1156 tests passed**，保留原有代理回归测试 | `node/test-final.log` |
| 前端类型检查 | `npm run typecheck` | 通过 | `node/typecheck-final.log` |
| 前端纪律检查 | `sh scripts/check-discipline.sh` | 通过 | `node/discipline.log` |
| Next.js 生产构建 | `npm run build` | 修复后的业务源码构建通过；无字体 mock 或依赖降级 | `node/build-fixed.log` |
| 生产构建版 Next → Go → HTTP 测试上游 | Cookie / Origin / 请求体、多 Set-Cookie、OAuth 302、DLC body/header 范围拒绝 | 均通过 | `node/proxy-chain-final.log` |
| SSE 首段及时输出 | Go TCP/内存 HTTP，以及生产构建版 Next → Go 链 | 通过，读取首段时上游未结束响应 | `go/race-tcp.log`、代理脚本 |
| Go 请求取消 | 响应头之前取消、SSE 首段之后断开 | 两种真实 TCP 场景通过，上游 context 结束 | `go/race-tcp.log` |
| 完整入口 SSE 断开 | 客户端 TCP 断开后等待 2 秒，检查上游响应关闭 | **失败**；移除 Go 后也失败 | `node/proxy-chain-confirm.log`、`node/proxy-baseline.log`、`node/proxy-chain-final.log` |

Go/FastAPI 测试运行真实 Go 二进制、Uvicorn、FastAPI 和临时 SQLite；Next 生产入口测试使用可观测的 HTTP 测试上游。两者是互补验证，没有将它们描述为一次完整的浏览器 → Next → Go → FastAPI 端到端运行。

## 兼容性和安全检查

- 原 `/api/characters` 与直接 Python 响应一致；SC 作用域目录保留原展示数据。`sc:mano` 与 `demo:mano` 分离，DLC 世界设定独立，未实现的 DLC RAG 返回 501。
- Python 建立的旧裸角色 ID 对话，经 Go 读取列表与历史消息均相等；通过 Go 发起文字回合得到 `user` 至 `done` 的 SSE 事件并写入同一临时库。
- 匿名历史读取仍返回 401；已登录写请求的恶意 Origin 和缺失 Origin 均返回 403；新 OAuth 用户未同意条款仍返回 428。
- GitHub fake provider 完成 start/callback；错误 state 被拒绝；302 Location 和 HttpOnly / SameSite 会话 Cookie 保留。生产构建版 Next 入口另验证多条 Set-Cookie 透传。真实 GitHub 登录和生产 Secure Cookie 浏览器行为未现场验证；Python 原有会话与身份提供方回归套件通过。
- 全量 Python 回归包含已有数据库 schema、历史、导入导出、会话、安全与通话回归；未对任何真实生产数据库副本运行迁移或恢复测试。Go 仍没有 SQLite 连接或迁移职责。
- 显式 DLC body 范围被拒绝；本轮补齐 Next 对 `X-Work-ID` 的透传，使显式 DLC header 范围同样在到达旧 SC 上游前返回 409。

## 本轮修改文件

| 文件 | 变更 |
|---|---|
| `frontend/src/app/api/[...path]/route.ts` | 转发 `X-Work-ID`，防止经过 Next 后作品范围丢失、误入 SC |
| `frontend/src/app/api/[...path]/route.test.ts` | 保留原测试，新增范围透传回归；修复前断言得到 201 而非 409，修复后通过 |
| `backend/tests/test_go_catalog.py` | 排除值为 None 的 Cookie，修复 pyright 错误；增加 SSE、历史消息、Origin、OAuth、Cookie 和条款检查 |
| `core/internal/catalog/proxy_acceptance_test.go` | 增加真实 TCP 上的响应头前取消及 SSE 断开测试 |
| `docs/migration/acceptance-2026-10-24.md` | 本报告 |
| `docs/migration/go-dlc-first-slice.md` | 添加本次验收报告链接，保留此前记录 |
| `.scratch/dlc-acceptance-20261024/` | 初次失败、修复后日志及可复验代理脚本 |

开始时已有的 CI、GLOSSARY、Compose、ADR 等未提交改动没有被本轮覆盖或提交。

## 失败过程与剩余风险

1. **测试环境问题，已解决。** 最初容器没有任何非回环接口，aioice 候选地址为 `[]`，全量 pytest 得到 71 failed / 1964 passed / 1 skipped，失败均涉及通话连接。接入内部网络后先验证通话测试，再完整重跑得到 2035 passed / 1 skipped。未修改业务代码来绕过通话测试。
2. **安装及构建环境问题，已解决。** 前端初次测试遗漏角色配置，补齐只读源码副本后通过。关闭外网时 Google Fonts 下载失败；恢复 bridge 后仍有四个字体文件临时下载失败，再次正常构建成功。Python pyright 首次因缺少 Node 且断网无法下载启动失败；将 Node 容器中的 Node 二进制直接复制到 Python 容器后运行成功，宿主机未安装 Node。
3. **重构问题，已修复。** 新 Go 入口依靠作品范围防止 DLC 回退 SC，但旧 Next 头部白名单遗漏 `X-Work-ID`。生产构建测试复现错误 200，新增回归测试复现错误 201；修复后生产入口返回 409 且上游请求计数不变。
4. **重构测试问题，已修复。** CookieJar 中 value 类型为 `str | None`，不符合 HTTPX 的 cookies 字典类型；过滤 None 后 pyright 通过。
5. **仍失败：完整入口取消传播。** 上游输出一段 SSE 后保持静默，客户端收到首段后主动断开 TCP，Next 未在 2 秒观察窗口内关闭上游响应。Next → 测试上游和 Next → Go → 测试上游均复现；该 Next 代理源码在重构前基点与验收开始时相同。Go 直接代理的取消测试通过。可能导致浏览器离开后上游工作继续，特别是在下一段输出尚未到来时；没有把此观察夸大为永久泄漏。按本次仅修复重构引入问题的范围保留，**全链路取消验收未通过**。
6. 未进行真实 OAuth/LLM/TTS 服务调用、浏览器 Playwright 视觉验收、真实用户数据库副本回归、生产反向代理/Caddy 部署、Linux amd64/1GB VPS 压测；Linux arm64 上的结果不能替代这些验收。没有第二批迁移或生产部署。

代理复验脚本在 `.scratch/dlc-acceptance-20261024/proxy-acceptance.mjs`，使用容器内固定 `/work` 路径、测试端口 18000–18002、假凭据与 `.invalid` OAuth URL。`PROXY_BASELINE=1` 会让 Next 直连测试上游；该模式下 DLC 拒绝断言也会失败，这是缺少 Go 范围守卫的预期，仅用于对照最后一项 SSE 取消行为。

## 磁盘和宿主机文件

Docker Engine 统计（十进制 GB；不等同于 macOS 上 OrbStack 稀疏磁盘文件的实际物理增量）：

| 类别 | 验收前 | 验收后 | 新增 |
|---|---:|---:|---:|
| 镜像 | 1.587 GB / 4 个 | 4.808 GB / 7 个 | 约 3.221 GB / 3 个 |
| 容器可写层 | 245.8 kB / 2 个 | 4.322 GB / 5 个 | 约 4.322 GB / 3 个 |
| 命名卷 | 196.3 MB / 1 个 | 不变 | 0 |
| Docker build cache | 103.1 MB / 12 条 | 不变 | 0 |

**新增 Docker 逻辑占用约 7.54 GB。** 三个验收容器可写层分别约为 Go 262,221,824 bytes、Python 2,901,975,040 bytes、Node 1,157,677,056 bytes。缓存都在容器内，无新增命名缓存卷：uv 缓存约 1.10 GB，Python venv 约 1.12 GB，npm 缓存约 179 MB，node_modules 约 706 MB，`.next` 约 243 MB；这些已计入可写层，不重复相加。

宿主机确实产生了额外文件：上述源代码与报告改动、`.scratch/dlc-acceptance-20261024/` 约 1.12 MiB，以及 `/tmp/eidolon-accept-go-results-20261024/`、`/tmp/eidolon-proxy-acceptance.mjs`、`/tmp/eidolon-scope-test-20261024.ts` 合计约 24 KiB。没有在宿主机创建依赖目录或构建产物。未扫描整个主目录来推断 Docker/OrbStack 自身的辅助文件变化。

## 保留资源与手动清理方法

三个验收容器已停止，所有源码副本、依赖、测试临时数据库、缓存及日志仍保留；原有 `my-mysql`、`my-redis` 容器与原有卷未操作。没有执行 prune 或删除资源。

Node/Python 容器的常驻 PID 1 是 `sleep infinity`，手动 `docker stop` 后显示 exit 137；两者 `OOMKilled=false`。这不是测试失败或内存溢出，测试退出码已在停止容器之前分别记录。

在确认不再需要复验后，可由用户执行以下精确清理命令；本轮**没有执行**：

```sh
docker rm eidolon-accept-go-20261024 eidolon-accept-python-20261024 eidolon-accept-node-20261024
docker network rm eidolon-accept-internal-20261024
docker image rm golang:1.27 node:22-bookworm ghcr.io/astral-sh/uv:python3.11-bookworm-slim
```

镜像如已被其他新容器使用，保留即可，不要强制删除。删除验收容器会一起删除其依赖缓存和临时数据库；仓库日志及 `/tmp` 文件不会因此删除，可以分别人工删除。修复代码和验收报告应保留。
