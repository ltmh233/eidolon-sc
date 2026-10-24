# 第一阶段收尾：Python 全量失败排查与复验

后续最终审查与修复：[两项 P2 修复与复验](acceptance-p2-2026-10-24.md)。以下保留本轮当时的结果及结论。

日期：2026-10-24，Asia/Shanghai。Mac 本地仓库，`refactor/multi-work-dlc`，HEAD `201fe209`。本报告补充 [原工程验收](acceptance-2026-10-24.md) 和 [SSE 验收](acceptance-sse-2026-10-24.md)，保留两份报告的历史失败及当时结论。

**结论：两项测试时序问题已修复，两轮完整 Python 回归及静态检查通过。结合此前已通过的 Go、前端及生产构建 SSE 验收，可以宣布第一阶段在约定的 Docker 隔离、模拟外部服务测试范围内工程验收完成。不是生产部署或真实外部服务验收。到此停止，不开展第二阶段迁移。**

## 根因与修复边界

两项失败均为测试在异步业务完成之前作出断言。本轮没有发现这两个案例中的持久化丢失或重复插入，无需改动 Python/Go/Next 业务代码或 SQLite schema。

### 通话结束记录

原测试收到 `ended`、Pipecat `peerLeft` 并关闭浏览器后，立即用独立 SQLite 连接读取 `activity_records`。但这只确认传输层断开，不确认后台通话结算完成。

实际安装的 Pipecat 1.12.0 在 output transport 的 `stop/cancel` 中执行 `_teardown` 并断开连接；项目 `_LiveCall._serve` 要等 `_run` 返回后才在 finally 中执行 `CallOutcome.settle`，后者完成活动记录和通话结算。`_live` 集合由服务任务的 done callback 移除。因此在 `peerLeft` 后存在数据库尚未写完的合法窗口。项目已有 `tests.browser.until_gone` 正是为此等待 ICE 入口消失及 `calls.in_progress == 0`。

复现探针只在 `Recording.finish(kind='call')` 前增加 200 ms 异步延迟，原测试连续 5 次得到 `[]`；随后等待真正的 finish 完成，5 次数据库均为 `[('character_hung_up',)]`，没有丢记录。

最小修复：保存 offer 响应的 `pc_id`，在原数据库断言之前调用已有 `until_gone`。没有删除记录断言、吞异常或给生产代码加等待；helper 原有 10 秒上限未改变。如果结算后记录仍丢失，原断言依然失败；如果服务任务悬挂，helper 超时失败。

### 记忆导入去重

原测试在文字回合 `done` 后立刻导入数据包，假设后台模型已经提取并写入东京记忆。实际上回合结束与后台记忆落库是两个时点：`Memories` 创建后台任务，`DataPackages.import_` 则按导入事务当时已经存在的记忆去重。尚未持久化的模型结果不是“已有记忆”。

两条写入路径都在 ADR-0028 单写队列的事务内按用户和文本去重：导入用 `add_carried`，后台提取用 `Memories._add`。当导入先发生时，返回新增 3 条是正确的当时计数，后续模型提取不会再次插入同文本。

自然重复中 40 次有 2 次命中原始 `3 != 2`。将 fake 后台模型延迟 200 ms 后连续 5 次复现；等待后台 `_write` 完成后直接查询临时 SQLite，3 个文本各 1 条，没有重复行。单进程同样复现，排除必须依赖 xdist 交叉污染的解释。

最小修复：导入前调用现有记忆列表 API，并明确断言此时只有东京记忆。该 API 已有等待当前后台任务的语义（5 秒上限未改），这建立了原测试“already here”的前提。原有导入新增数 2、取代关系、排序、二次导入新增数 0、删除后的关系恢复断言全部保留。既没有让导入接口等待模型，也没有改变并发导入的计数语义。

## 可复现证据

材料目录：`.scratch/dlc-python-final-20261024/`，含 README、原始日志和最终诊断脚本。两个探针调用原测试，200 ms 只用于放大调度窗口，不是修复手段。探针额外等后台操作结束并查询临时数据库；无真实用户数据。

最终同一份探针的对照：容器副本仅移除两处新增等待，**10 failed / 0 passed**；恢复最终测试源码，**10 passed / 0 failed**。每项各重复 5 次，均为单进程。完整测试前诊断文件移出 `tests/` 并保留在 `/results/python-final/`，不混入全量统计。

初版包装器漏了通话模块 settings fixture，导致通话超时；补充探针曾用错私有方法名，另曾忽略原测试最后主动删除东京，错误期待最终仍有 3 条。它们均在 README 和日志中标注为诊断脚本错误，不作为产品失败或根因证据。最终修正后重新完成同脚本红绿对照。

## 实际测试结果

| 项目 | 结果 | 日志 |
|---|---|---|
| 修复前自然重复，各 40 次 | 78 passed / 2 failed，均为记忆原断言 | `repeat-configured.log` |
| 最终探针，移除两处等待 | 0 passed / 10 failed，均为原断言 | `controlled-before.log` |
| 最终探针，恢复两处等待 | 10 passed / 0 failed | `controlled-after.log` |
| 修复后自然重复，各 40 次 | 80 passed / 0 failed | `repeat-after.log` |
| 两个原文件及 memory/data_access/call_outcome/Go-FastAPI | 104 passed / 0 failed / 0 skipped | `related.log` |
| 完整 Python 第 1 轮 | 2035 passed / 0 failed / 1 skipped，58.61 s | `full-1.log` |
| 完整 Python 第 2 轮 | 2035 passed / 0 failed / 1 skipped，59.87 s | `full-2.log` |
| ruff | All checks passed | `ruff.log` |
| pyright | 0 errors / 0 warnings | `pyright.log` |

完整命令为 `PYTEST_XDIST_AUTO_NUM_WORKERS=4 timeout 550 uv run --no-sync pytest -n auto -rs`。`PYTHONUNBUFFERED=1`；`EIDOLON_CORE_BINARY=/work/eidolon-core`，Go/FastAPI 测试实际执行。唯一平台跳过项是 `tests/test_windows_udp.py:24`，只适用于 Windows proactor。两轮各 121 条 warning 来自既有依赖弃用等提示，未静默过滤。

本轮修改仅限两个 Python 测试及验收材料，未重复前端测试、类型检查、构建或 Go race/vet。沿用上一份 SSE 报告已执行的前端 1158 passed、类型检查/生产构建通过、Go 单元/race/TCP/vet 通过、catalog 95.4%、生产入口 1610 passed（1600 次取消）结果，不能表述为本轮重跑。

Go/FastAPI 本轮相关及全量测试继续实际覆盖 SC 目录和旧历史、模拟 GitHub OAuth、Cookie、Origin 拒绝、条款、DLC 显式范围拒绝及 SSE 文字回合。Next 的多条 Set-Cookie、302、X-Work-ID、流式上传和断开传播沿用未改变代码的上轮证据。

## 修改文件

- `backend/tests/test_she_ends_conversation.py`：等待通话结算后检查活动记录。
- `backend/tests/test_data_package.py`：明确建立导入前已有记忆的前提。
- 本补充报告；前两份报告新增后续记录链接，原失败结果不变。
- `.scratch/dlc-python-final-20261024/`：复现脚本、日志、复跑说明及资源统计。

保留开始时已有的全部未提交改动；未提交、推送、创建 PR 或部署。没有开始第二阶段迁移。

## 资源与阶段结论

仅启动并复用已有 `eidolon-accept-python-20261024`；Node/Go 验收容器未启动，沿用已编译 Go 二进制进行跨语言测试。没有安装依赖，没有新增镜像、容器、网络、命名卷或构建缓存。Python 使用已有内部网络，无宿主机端口发布；未增加挂载或提权。测试结束已停止 Python 容器，三个验收容器均为 exited，所有资源保留。

Docker 可写层逻辑占用（bytes，上轮结束即本轮起点）：

| 容器 | 开始 | 结束 | 增量 |
|---|---:|---:|---:|
| Python | 2,913,177,600 | 3,413,270,528 | 500,092,928 |
| Go | 262,320,128 | 262,320,128 | 0 |
| Node | 1,162,829,824 | 1,162,829,824 | 0 |

净增 **500,092,928 bytes，约 476.93 MiB**，保留了本轮测试生成的临时数据库、日志与缓存等。这是 Docker 逻辑占用，未测量 OrbStack 稀疏磁盘在 macOS 上的实际物理分配增量。镜像仍为 7 个 / 4.808 GB，命名卷 1 个 / 196.3 MB，build cache 12 条 / 103.1 MB，均未增加；统计见 `docker-resources.txt`。

宿主机新增本报告和 `.scratch/dlc-python-final-20261024/`（约 1 MiB），修改了上述两个测试文件和两份报告的后续链接。没有在宿主机安装依赖、构建或执行语言测试，也没有在项目外创建本次产物。诊断脚本只在容器副本中执行，最终测试文件已用 `cmp` 确认为宿主机最终版本。

未执行资源清理。若用户之后决定丢弃 Python 验收环境，可手工执行 `docker rm eidolon-accept-python-20261024`，会一并删除该容器原有依赖、缓存、临时数据库及日志；先保存所需材料。其他既有验收容器、镜像和网络的精确清理命令仍见原报告，本轮没有新增需要额外清理的资源。项目源码修复与验收报告应保留，scratch 日志是否保留由用户决定。

剩余边界延续前两份报告：真实外部 OAuth/LLM/TTS、生产 Caddy、真实用户数据库副本、amd64/1GB VPS 压测未验证。测试使用模拟凭据和临时 SQLite，未读取真实 `.env`，未访问生产环境。
