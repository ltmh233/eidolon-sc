# 第一阶段收尾：SSE 断开传播修复与补充验收

日期：2026-10-24，Asia/Shanghai。工作分支 `refactor/multi-work-dlc`，HEAD `201fe209`。本报告补充此前的 [Docker 工程验收](acceptance-2026-10-24.md)，不替换此前失败记录。

后续收尾：[Python 全量失败排查与复验](acceptance-python-2026-10-24.md)。以下保留本轮当时的失败及结论；后续报告记录根因证据、最小修复和最终全量结果。

**SSE 遗留问题已修复，专项生产构建验收通过。第一阶段暂不能宣布“全部回归通过”：本轮两次 Python 全量测试各有一项不同的失败，单文件复验均通过，仍需独立调查其稳定性。** 没有为了获得绿色结果删除断言、放宽窗口或不断重跑全量套件。

## 根因及最小修复

实际版本：Next.js 16.4.0、Node.js 22.23.4，Linux arm64 容器。

1. 在上一轮生产构建运行原 `proxy-acceptance.mjs`，再次观察到客户端收到首段 SSE 后断开，而上游在 2 秒内没有关闭。
2. 仅在容器源码副本添加临时诊断监听器，重新构建。日志出现 `[SSE-PROBE] request aborted ResponseAborted`，与此同时上游关闭断言仍失败。这排除了“Next 完全没有暴露客户端断开”的假设。
3. 检查本项目安装的 Next 源码：
   - `next/dist/server/web/spec-extension/adapters/next-request.js` 中 `createAbortController` 在 Node response 的 `close` 早于正常完成时中止 signal；`writableFinished` 为 true 时不取消。
   - `next/dist/server/pipe-readable.js` 将响应流 pipe 到客户端，并在断开时取消这条流。
   - `next/dist/server/lib/dedupe-fetch.js` 对不带显式 signal 的 GET/HEAD Fetch 执行响应复制；`clone-response.js` 用 `body.tee()` 实现。只取消一个分支不能可靠关闭原始上游流；`cache: "no-store"` 不等同于退出这一层请求去重。显式 signal 是该实现的退出机制。
4. 项目代理遗漏了 `signal: request.signal`。因此 Fetch 在等待响应头时与下游生命周期脱离；GET 响应还可能留有另一个 tee 分支。实际对照中 POST 首段之后通常已能取消，但等待响应头时同样失败。

修复仅在 `RequestInit` 加入 `signal: request.signal` 及解释注释。没有修改 Go、增加超时、捕获并吞掉所有错误、手动缓冲响应或创建额外读取器。

这使中止直接到达 Fetch，涵盖等待响应头及读取响应体两个阶段，同时避免 GET 去重复制。继续直接返回 `res.body`，由现有 Fetch/Next 管理读取、背压、取消和锁；没有引入需要人工释放的 `getReader()` 或自定义流监听器。正常完成时 Next 不触发提前断开 signal；完整读取和上游异常均有独立测试。

临时诊断只在容器副本中使用，最终源码和生产构建已恢复为无诊断版本。原始诊断日志保留。

## 可复现对照与测试夹具

日志根目录：`.scratch/dlc-sse-20261024/`。

- `node/before.log`：原生产构建、原复验脚本再次失败。
- `node/probe.log`：真实断开已触发 Next request signal，但上游仍未及时关闭。
- `node/unit-before.log` → `node/unit-after.log`：新增两项单元测试从失败转为通过。
- `node/mutation.log`：最终版本夹具、仅在容器副本中移除 signal 后，生产构建得到 **16 passed / 10 failed**；失败包括两条链路的 GET 首段后 FIN，以及 GET/POST 响应头前 FIN/RST。未调整 2 秒阈值。
- 恢复项目源码并重新生产构建，`node/production-final.log` 得到 **1610 passed / 0 failed**；`node/original-final.log` 中上一轮原脚本全部通过。

新增可重复执行脚本：`frontend/scripts/accept-proxy.mjs`。脚本启动真实 Next standalone 构建、真实 Go 二进制及可观测 HTTP 测试上游，使用容器内回环临时端口。每个取消场景先确认上游尚未关闭，再触发 FIN 或 RST，观察上游响应实际 `close` 事件，确认它没有正常 `finish`。不会把客户端 Socket 自己关闭当作成功。

最终矩阵：两条链路 × GET/POST × 响应头前/首段后 × FIN/RST × 每种 100 次，共 **1600 次取消**；另有 4 项正常 SSE、2 项上游中途断开、2 项流式上传、2 组 HTTP 兼容检查，共 1610 项。

测试夹具曾暴露一个独立问题：初稿在 POST 请求体尚未读完时就发送 SSE。增加重复次数后，上游出现 `requestComplete=false, requestAborted=true` 的提前关闭，最初 330 项中失败 1 项，进一步诊断 1610 项中失败 14 项。它是在测试 HTTP/1 的同时上传/响应，不是实际 FastAPI 先解析 JSON 再响应的顺序。最终 SSE 夹具先消费请求体；独立上传测试仍要求**上传未完成时上游就已收到第一段**，所以没有用缓冲上传绕过检查。提前关闭断言未删除，最终每次取消前都要求上游仍然存活。初稿和诊断日志保留，未将其负延迟样本计入最终测量。

容器内复验命令（前端已构建，Go 二进制和内容包沿用此前副本）：

```sh
cd /work/frontend
EIDOLON_CORE_BINARY=/work/eidolon-core SSE_ROUNDS=100 node scripts/accept-proxy.mjs
```

脚本默认每场景重复 3 次；100 次用于本轮重复性检查。观察窗口始终为 **2000 ms**，继承原失败标准，为本机毫秒级传输保留调度余量；没有通过提高阈值让测试变绿。首段测试由测试端在收到第一段后才允许上游发送第二段并结束，因此完整缓冲会直接卡住并失败。

## 最终延迟

取 `node/production-final.log`，同一进程的单调时钟计时：

| 链路 | 首段从上游 write 至客户端收到（GET/POST，共 2 次） | 取消延迟范围（800 次） | 取消 P95 |
|---|---:|---:|---:|
| Next → HTTP 上游 | 0.179–0.876 ms | 0.077–3.703 ms | 0.417 ms |
| Next → Go → HTTP 上游 | 0.191–0.642 ms | 0.081–2.186 ms | 0.437 ms |

取消延迟从客户端调用 Socket FIN/RST 到上游 response close。首段数字不包含模型生成耗时，也不是生产端到端 TTFT。正常 SSE 的两段及 done 事件内容和顺序逐字比较，要求完整结束；上游中途异常则要求下游得到不完整响应并结束读取。预期异常注入会在 Next 日志中留下 `failed to pipe response`，不等于测试失败。

## 回归结果与兼容性

| 项目 | 本轮实际结果 | 日志 |
|---|---|---|
| 原始代理复验脚本 | 全部通过 | `node/original-final.log` |
| 生产构建最终矩阵 | 1610 passed / 0 failed / 0 skipped | `node/production-final.log` |
| 前端全量 Vitest | 77 files，1158 passed / 0 failed | `node/frontend-tests.log` |
| 前端类型检查 | 通过 | `node/typecheck.log` |
| 前端纪律检查 | 通过 | `node/discipline.log` |
| 最终 Next 生产构建 | 通过，正常字体下载，无 mock、无版本变更 | `node/build-final.log` |
| Go 单元、race、TCP、vet、编译 | 全部通过，catalog 覆盖率 95.4% | `go/` |
| Go ↔ FastAPI | 1 passed / 0 skipped；全量中也执行 | `python/cross-language.log` |
| ruff、pyright | 通过；pyright 0 errors / 0 warnings | `python/ruff.log`、`python/pyright.log` |
| Python 全量第 1 轮 | **2034 passed / 1 failed / 1 skipped** | `python/pytest.log` |
| 通话失败文件单独复验 | 15 passed | `python/python-targeted.log` |
| Python 全量第 2 轮 | **2034 passed / 1 failed / 1 skipped**，失败项与第 1 轮不同 | `python/pytest-final.log` |
| 数据包失败文件单独复验 | 19 passed | `python/python-data-package.log` |

两次全量均使用 `timeout 550 uv run --no-sync pytest -n auto -rs`，`PYTEST_XDIST_AUTO_NUM_WORKERS=4`、`EIDOLON_CORE_BINARY=/work/eidolon-core`。跳过项始终是 Windows proactor 专属测试。没有把两次各自不同的通过项拼成一次“全量通过”。

生产入口检查覆盖 200/401/403/404/428/500 状态码、Cookie、Origin、SC `X-Work-ID`、多条 Set-Cookie、OAuth 302 Location、请求体内容和流式上传；经 Go 的 DLC body/header 范围返回 409。跨语言测试继续覆盖真实 FastAPI 上的认证、条款、Origin 拒绝、SC 目录/历史消息与 SSE 回合。原有前端 avatar/304/数据包代理回归全部保留并通过。

没有更改普通 JSON、OAuth 或认证语义；没有新增数据字段或改动 SQLite schema。

## 本轮修改文件

1. `frontend/src/app/api/[...path]/route.ts`：新增 Fetch signal 及注释；保留上一轮的 `X-Work-ID` 修复。
2. `frontend/src/app/api/[...path]/route.test.ts`：新增等待上游时取消、正常完成不取消两项测试，保留此前全部测试。
3. `frontend/scripts/accept-proxy.mjs`：新增生产构建回归与延迟测量脚本。
4. 本补充报告；原报告仅新增链接，保留历史结论。
5. `.scratch/dlc-sse-20261024/`：原始失败、诊断、反向验证、最终测试日志。

Go 和 Python 源码/测试本轮均未修改。开始时已有 CI、GLOSSARY、DLC、测试改动以及 `docs/.DS_Store` 等文件均保留。没有提交、推送、创建 PR 或部署。

## 剩余风险与阶段结论

SSE 取消缺陷已通过生产构建、原脚本、反向验证和 1600 次主动取消检查关闭。没有当前证据要求增加自定义 reader.cancel 包装或修改 Go。

但两次 Python 全量失败必须保留：

- 第 1 轮：`tests/test_she_ends_conversation.py::test_a_call_in_it_as_she_ends_it_she_hangs_up_telling_it_she_ended_it`，期望通话结束记录 `['character_hung_up']`，实际为 `[]`。
- 第 2 轮：`tests/test_data_package.py::test_a_memory_already_here_is_not_added_again_and_stands_in_for_it`，期望新增记忆数 2，实际为 3。

两者都使用直接 ASGI/fake 后端，不经过本次修改的 Next 代理；相关 Python 代码未变，单文件分别 15/15、19/19 通过。这支持“与本次 signal 修改无直接路径关联、可能存在异步收尾时序问题”的判断，但**尚未证明它们只是无害的测试波动**。涉及通话持久化和记忆去重，应独立调查；本轮不越界修改这些模块。不能据单独复验通过而抹掉全量失败。

因此：**可以宣布 SSE 专项修复验收完成；暂不能无条件宣布第一阶段全部工程验收完成。** 剩余障碍是上述 Python 全量稳定性，而不再是 SSE 断开传播。没有开始第二阶段 Go 迁移。

其他边界延续此前：真实外部 OAuth/LLM/TTS、生产 Caddy、真实用户数据库副本、amd64/1GB VPS 负载未验证。本次没有读取真实 `.env` 或访问生产环境。

## Docker 资源与宿主机文件

复用原有三个验收容器、三个镜像和内部网络。**本轮新增镜像、容器、命名卷、Docker build cache、网络均为 0。** 未安装新依赖。只在 Next 构建取字体时接入普通 bridge，代理测试断开外网；Python 保持原内部网络，无宿主机端口发布或提权配置。

容器可写层统计（bytes）：

| 容器 | 本轮开始 | 本轮结束 |
|---|---:|---:|
| `eidolon-accept-go-20261024` | 262221824 | 262320128 |
| `eidolon-accept-python-20261024` | 2901975040 | 2913177600 |
| `eidolon-accept-node-20261024` | 1157677056 | 1162829824 |

净增 **16,453,632 bytes，约 15.69 MiB**，来自日志、临时测试数据和重建产物等；这是 Docker 逻辑占用，不是 OrbStack 稀疏磁盘物理分配量。镜像仍为 4.808 GB，命名卷 196.3 MB、build cache 103.1 MB 不变。

宿主机新增上述源码/报告与 `.scratch/dlc-sse-20261024/`（约 1.29 MiB 日志）；没有宿主机依赖安装、编译或测试。三个验收容器已停止，依赖、数据库及缓存继续保留，原用户容器和卷未操作。未执行任何 prune 或删除。

若之后用户决定清理，仍使用原报告中的精确 `docker rm` / `docker network rm` / `docker image rm` 命令；本轮没有新增需要额外删除的 Docker 资源。仓库日志可单独人工清理，修复与报告应保留。
