# 第一阶段最终审查：两项 P2 修复与复验

日期：2026-10-24。分支 `refactor/multi-work-dlc`，HEAD `201fe209`。承接 [Python 收尾报告](acceptance-python-2026-10-24.md) 后的最终审查。本报告不替换此前测试结果，也不把历史通过记录当成本轮执行结果。

**结论：本轮两项 P2 已复现并修复，相关回归及 Python 全量通过。取消故障注入依旧被断言识别为失败，同时能可靠退出。第一阶段可在既定 Docker 隔离、模拟外部服务范围内完成工程验收；不等于生产部署验收。停止于第一阶段。**

## 文字回合请求体作用域

修复前，Go 只检查创建对话、offer 的 JSON 作用域。文字回合的 `TurnInput` 忽略额外 `work_id`，所以已认证请求向有效 SC 对话发送 `{"text":"不可写入 SC","work_id":"demo"}`，没有 query/header scope 时，会运行 SC 回合。新增真实 Go → Uvicorn/FastAPI 反例得到 **200，而非预期 409**，见 `python/scope-before.log`。

最小修复位于 Python 已有 JSON 输入边界，而非在 Go 增加读取和缓冲：`TurnInput` 保留 `work_id`，默认 `sc`；路由在查找对话、调用 admission、模型和写历史之前拒绝非 `sc` 值，返回 409。显式 null 也拒绝；省略或显式 `sc` 沿用旧行为。认证仍由原 FastAPI 依赖执行。本次没有迁移运行时所有权、增加 Go 请求大小限制或改变上传流式转发。

扩展既有跨语言测试，分别经 Go 和直连 FastAPI 验证 `demo`、null 均返回 409，历史保持不变且 fake LLM 未收到 prompt；随后验证省略作用域及显式 `sc` 的合法回合仍完成 SSE。原 OAuth、Cookie、Origin、条款、目录与历史断言保留。

这里检查的是文字回合的顶层 `work_id`；没有把本轮证据扩大为所有旧接口、任意 JSON 深度均有统一作品校验。Go 的创建/offer body guard、全路径 query/header guard 保持原实现。

## 取消测试故障注入与清理

首次探针在网关入口把请求换成 `context.Background()`，测试仍通过。它没有证明上游取消已被隔离，因此 **不计作故障验证**，日志保留为 `go/cleanup-ineffective.log`。

正式探针移到实际发往上游的 `RoundTripper`：仅在容器测试副本中，把该请求 context 换成 Background，然后交给真实 `http.DefaultTransport.RoundTrip`。日志明确记录 `[CANCEL-PROBE] upstream context Done nil: true`，不改产品 Handler。两个独立执行均观察到：

1. 原 2 秒窗口触发 `upstream work survived client disconnect`；
2. `httptest.Server blocked in Close after 5 seconds`；
3. 最终 `panic: test timed out after 8s`。

见 `go/cleanup-before.log`、`go/cleanup-before-repeat.log`，以及保存的 `go/injected-before.go`。这才是有效的清理挂起复现。

修复为上游 handler 增加独立 `release` 通道，退出时先关闭 release，再执行两个 server.Close。只有真实请求 context 取消才关闭 `stopped`；release 分支不会制造“取消成功”。因此失败清理不再依赖被测取消能力，也不会让取消失败变绿。

相同 RoundTripper 注入保留，头前和首段后各重复两次：**四个子案例仍全部失败，每个约 2.01 秒，整个测试 8.038 秒正常以失败状态退出，无 Close 挂起或 panic**。总测试保护为 15 秒以容纳四个 2 秒失败，单项观察窗口始终是 2 秒；不是扩大窗口让测试通过。见 `go/cleanup-after-injected.log` 和 `go/injected-after.go`。

随后从项目恢复无注入测试源码，再执行 Go 全量。故障注入文件仅保留在日志目录，未进入产品代码或正常测试路径。

## 本轮实际结果

日志根目录：`.scratch/dlc-p2-20261024/`。

| 项目 | 实际结果 | 日志 |
|---|---|---|
| 作用域修复前跨语言反例 | 1 failed：200 != 409 | `python/scope-before.log` |
| 作用域修复后跨语言 | 1 passed | `python/scope-after.log` |
| 无效取消注入 | 1 个子案例通过，未命中故障，不作修复证据 | `go/cleanup-ineffective.log` |
| 有效注入、清理修复前 | 两次均 2 秒断言失败、8 秒进程 timeout | `go/cleanup-before*.log` |
| 有效注入、清理修复后 | 4 个子案例如期失败并正常退出，无挂起 | `go/cleanup-after-injected.log` |
| Go 单元 | 通过；未开 TCP 的普通运行保留其环境跳过 | `go/unit.log` |
| Go race / TCP / coverage | 通过，catalog 95.4%，两种取消场景均执行 | `go/race-tcp.log` |
| Go vet / build / gofmt 差异检查 | 通过，无格式差异 | `go/vet.log`、`go/build.log`、`go/gofmt.log` |
| Python 相关（文字回合、跨语言、前轮两个失败文件） | 43 passed / 0 failed | `python/related.log` |
| Python 全量 | 2035 passed / 0 failed / 1 skipped，59.71 s | `python/full.log` |
| ruff / pyright | 通过；pyright 0 errors / 0 warnings | `python/ruff.log`、`python/pyright.log` |
| 已有 Next 生产构建 SSE/HTTP 矩阵 | 58 passed / 0 failed，含 48 次主动取消 | `node/production.log` |

Python 全量使用 `PYTEST_XDIST_AUTO_NUM_WORKERS=4 timeout 550 uv run --no-sync pytest -n auto -rs`，`PYTHONUNBUFFERED=1`、`EIDOLON_CORE_BINARY=/work/eidolon-core`；唯一 skip 是 Windows proactor 专属测试。121 条既有 warning 未过滤。跨语言使用已有 Go 二进制；本轮 Go 产品源码未变，仅修改 Go 测试。

生产矩阵沿用已构建 Next 和已有 Go 二进制，`SSE_ROUNDS=3`，两种拓扑、GET/POST、头前/首段后、FIN/RST，仍观察上游实际 close、排除正常 finish。首段测量 direct GET/POST 为 1.016/0.651 ms，Go 链路为 0.654/0.654 ms。包含正常完成、上游异常、未完成上传的转发及 HTTP 兼容。没有重建前端或重跑前端 Vitest/typecheck；前端代码未改，既有完整结果仍见 SSE 报告。

## 修改文件及边界

- `backend/src/eidolon/app.py`：保留文字回合作品范围，在业务执行前拒绝非 SC。
- `backend/tests/test_go_catalog.py`：扩展已有真实跨语言测试，不覆盖此前改动。
- `core/internal/catalog/proxy_acceptance_test.go`：独立清理通道。
- `core/README.md`：说明文字回合作用域由 Python 校验，不增加 Go 缓冲。
- 本报告、前轮报告的后续链接、`.scratch/dlc-p2-20261024/` 证据和复跑说明。

两处前轮 Python 时序修复保持原样，相关及全量均重新执行。没有改 schema，没有真实用户数据、真实 `.env` 或外部服务调用，没有提交、推送、PR 或部署。现有未提交改动保留。

## Docker 与宿主机资源

全部安装/构建/语言测试在 Docker；未安装新依赖。复用 Python、Node 容器和已有镜像。原 Go 容器固定命令会执行后退出，因此新增 `eidolon-accept-p2-go-20261024`，使用已有 `golang:1.27` 镜像，`--network none`，只把项目 `core` 只读挂载到 `/source`，无端口、提权或 Docker socket 挂载。Python 沿用内部网络以支持 WebRTC 本机候选。

最终三只本轮运行容器均已停止；原 Go 容器仍停止。新增镜像、网络、命名卷、Docker build cache 均为 0，新增测试容器 1 个，其可写层 **263,032,832 bytes（250.85 MiB）**，包含源码副本、Go 编译缓存、日志和二进制。

既有 Python 可写层由 3,413,270,528 变为 2,918,707,200 bytes；Node 由 1,162,829,824 变为 1,162,846,208 bytes。计入新增 Go 容器，总可写层净变化为 **-231,514,112 bytes（减少 220.79 MiB）**。净变化不等于新增产物总量：pytest 会维护自己的临时目录保留集，本轮未手动删除或 prune 任何资源、缓存或用户数据。上述为 Docker 逻辑占用，不是 OrbStack 稀疏磁盘的实际物理增量。

镜像仍 7 个 / 4.808 GB，命名卷 1 个 / 196.3 MB，build cache 12 条 / 103.1 MB；容器总数由 5 增至 6。宿主机新增本报告及约 0.1 MiB scratch 材料，修改上述源文件；未在项目外生成本轮产物，未在宿主机安装依赖或运行构建测试。

未执行清理。用户之后若决定删除新验收容器，可手工执行 `docker rm eidolon-accept-p2-go-20261024`，将删除其独立编译缓存和日志；项目内证据已保存。其他原有容器及资源仍按前轮报告处理。

剩余验证边界不变：真实外部 OAuth/LLM/TTS、生产 Caddy、真实用户数据库副本、amd64/低配 VPS 压测未执行。本轮结论不覆盖这些环境。

## 后续 P3：循环断言输入标识

2026-10-24 最终复审提出一项 P3：跨语言作用域测试的循环断言未标注输入。已在 `backend/tests/test_go_catalog.py` 为入口分别命名 `gateway`、`direct`，并以 `scope={scope!r}` 区分 `'demo'` 与 `None`。状态码、历史不变、未调用模型三条断言均带此标识；状态码断言继续保留响应文本。请求、循环顺序、输入集合、断言条件和业务代码均不变。

复用已有 Python Docker 容器、依赖和 Go 二进制，在临时数据库及模拟服务下实际执行：

| 检查 | 结果 | 日志（`.scratch/dlc-p3-20261024/`） |
|---|---|---|
| `timeout 90 uv run --no-sync pytest tests/test_go_catalog.py -q` | 1 passed / 0 failed / 0 skipped，0.87 s | `related.log` |
| `uv run --no-sync ruff check .` | All checks passed | `ruff.log` |
| `uv run --no-sync pyright` | 0 errors / 0 warnings | `pyright.log` |

本次仅改变失败提示，未重跑完整 Python、Go 或前端套件；上文的全量结果仍属于 P2 修复验收。容器内测试文件已与宿主机最终源码通过 `cmp` 核对。没有新增 Docker 镜像、容器、卷或网络，没有安装依赖；Python 容器已停止并保留，可写层由 2,918,707,200 增至 2,918,719,488 bytes，净增 12,288 bytes（12 KiB）。宿主机只修改测试及本报告、保存上述日志。未清理资源、提交、推送或部署，现有改动保留。
