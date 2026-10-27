# 第三阶段 R2：管理员补发持久幂等

日期：2026-10-27；分支refactor/multi-work-dlc，基线 `04e47006ea13a08b4f2d89b4226bd62629e5473b`。开工已跟踪工作区干净，已有scratch、.DS_Store、docs/ideas/保留。规格与审查澄清在 `.scratch/reset-card-grant-idempotency/spec.md` 和 `issues/01-persistent-grant-idempotency.md`；日志也保存在该目录。本报告仅描述本地合成数据工程验收，不构成生产迁移授权。

## 方案与实际行为

管理员补发仍为“给全部现有用户各一张”，包括管理员/封禁者，后来注册者不追补；没有增加作品钱包或奖励经济规则。Python仍是唯一SQLite业务读写入口，Go仅代理，Next转发。

新增可选请求头Idempotency-Key，值为小写规范UUID v4。Python对重复header（即使同值）、空/组合/非法格式返回400；R1非SC声明仍409，Admin/Origin检查保持。端点继续无body参数，响应仍只有granted_at/count。

同一ID：一个事务内先查reset_card_grants，存在就返回原时间/人数，不notice、不重算、不发卡；权限每次验证。玩法关闭时也能核对已提交结果。新ID：关闭返回原409且不保存成功记录，开放后同ID可首次成功；开放时notice、初始化余额、加卡、写结果在同一BEGIN IMMEDIATE事务，失败全回滚。两个不同ID仍是两次合法新补发。

**旧客户端没有header时，每次由服务器产生新ID，仍各补发一次，不享有请求重试去重保证。** ID不是权限凭证；服务端不保存操作者或收件人身份。原grant记录及随机ID仍属于NOT_USER_DATA，全局注销不删除匿名补发历史。

前端在确认后、发送前，按当前账号在localStorage保存一项随机ID；每项独立key，刷新/重开后可恢复。响应未知时保留，核对沿用旧ID；确认新发放才新ID。账号切换会在POST前被getAccount比对阻止；存储失败不降级无ID调用。已成功POST后的GET刷新失败只影响展示，不恢复pending或再次发放。旧意图核对有独立按钮，玩法关闭时仍可用，新发放按钮保持关闭。手动删除浏览器存储或完全丢失ID时无法推断重复意图；不能承诺这种情形去重。

Next只在现有请求头白名单增加idempotency-key，显式空值也保留，防止被删后落入旧客户端路径；不新增body缓冲或改变SSE。真实HTTP的OWS按传输规范处理，不声称能识别代理已剥离的原始空白。Go源码未修改，原代理保留此头。

## Schema v3与离线升级

`grant_schema.py`由fresh安装及work_schema离线迁移共用：原reset_card_grants追加operation_id TEXT；非NULL唯一索引；新INSERT要求非空，UPDATE结果不可变，INSERT OR REPLACE不能替换已完成操作。历史行保留NULL，重复时间/数量不归并，原rowid不改。

VERSION=3，旧v2库启动明确拒绝并要求离线迁移。v0/v1/v2均通过原一致性备份、独占新备份路径、原列/序列摘要、事务和完整性检查链路升到v3；没有补发表的合法v2只建空结构，不初始化余额、不发奖励。未知结构/触发器、非法历史数量或日期明确拒绝，保留原库。新库由Store原子安装最终结构，Gifts不自行升级。

冻结夹具 `backend/tests/fixtures/sc-grants-v2.sql`由基线v2代码和原合成v1记忆夹具生成，含2条完全相同旧补发，保留其原rowid/时间/数量且新ID为NULL；不是生产导出。迁移也不重嵌入记忆、不重算成就、不运行历史回填奖励。

迁移进程在重建后直接退出和“disk is full”异常注入均验证源库仍v2且dump不变、备份完整，再执行可成功。磁盘满是错误注入，未填满宿主/真实磁盘；未模拟物理断电。恢复须停写、恢复匹配备份+应用，不改user_version降级；恢复旧备份会丢失备份之后的操作ID/结算，不能自动对账。没有执行生产迁移。

## 红灯、修复与日志

| 证据 | 实际结果/说明 |
|---|---|
| http-red.log | 2 failed：同ID并发重复发放；关闭玩法后不能重放。 |
| http-green.log | 7 passed：最小持久查询路径及原管理员补发测试通过。 |
| schema-red.log | 1 failed：v2旧库未被启动门禁阻止。 |
| schema-green.log | 8 passed：v3门禁/旧行无损与HTTP专项通过。 |
| backend-slice.log | 57 passed：原会话/记忆迁移、幂等与管理员专项。 |
| faults.log | 7 passed：迁移中断、磁盘满注入及HTTP故障专项。 |
| persistence.log | 8 passed：跨OS进程读持久结果及鉴权/Origin/SC边界；随后补充取消等待、不同ID并发等证据纳入全量。 |
| frontend-red.log | 新持久意图模块不存在，测试收集失败；实现后frontend-green.log为2 passed。 |
| header-red.log | 1 failed：空幂等头在代理被丢弃；修复后前端专项9 passed及类型检查通过（frontend-slice.log）。 |
| frontend-build.log | Node容器此前断网，字体下载失败；临时恢复普通bridge下载后frontend-build-retry/final.log通过。 |
| browser-install.log / browser-launch.log | 首次断网下载失败；下载成功后缺容器运行库。仅在容器安装本次浏览器及所需库，browser-launch-final.log通过。 |
| ui-first.log | 一次性脚本日志文件尚未打开就传给spawn；修复脚本，业务代码不变。 |
| ui-retry.log | 第一轮真实UI六场景通过。 |
| ui-final.log | 补强重跑复用了一次性假OAuth code，认证未完成；新建独立合成夹具后ui-final-retry.log七场景通过，旧夹具保留。 |

所有历史失败保留，不以跳过正式测试、放宽错误码或定时关闭请求掩盖问题。原容器内旧R1审计探针故意断言旧缺陷，仅它通过显式ignore排除，不属于仓库正式测试。

## 真实浏览器与三进程证据

新Next生产构建+实际Go+Uvicorn/FastAPI+临时SQLite，外部OAuth/模型全部合成：

- 浏览器第一次补发实际提交后截断响应，原ID保留；reload恢复pending。
- 关闭玩法后新发放禁用、核对按钮可用；同ID核对返回原结果。
- 再次有意确认产生新ID；POST成功后GET刷新500，pending不复活、不自动发第三次。
- localStorage写失败没有POST。
- `ui-sqlite.log`核对两条唯一操作ID、两位用户reset_cards各为2，integrity_check=ok；7场景输出在ui-final-retry.log，截图保留ui-artifacts/closed-replay.png。该UI使用新headless浏览器与真实业务SQL，不靠mock调用次数证明结算。
- 正式 `test_three_process.py` 两组资产配置通过（three-process.log，2 passed），检查Next头转发、关闭重放、空/组合键拒绝零业务写入，以及原R1、OAuth/Cookie、历史会话、SC记忆召回、SSE首段/取消。

UI验收只通过既有Docker内部网络通信，未发布宿主端口。下载时临时接普通bridge，下载结束即断开。所有一次性服务器由验收脚本或明确测试PID停止，临时库保留。

## 最终验收结果

| 范围 | 本轮实际结果 | 证据 |
|---|---|---|
| Python全量 | **2133 passed、1 skipped、117 warnings**，114.73秒；唯一skip为Windows专用测试 | python-full.log |
| 最终补发/迁移/原管理员专项 | **14 passed**，1.54秒 | final-targeted.log |
| ruff / pyright | 通过；**0 errors、0 warnings** | static-reviewed.log |
| 前端完整测试 | **1162 passed，78 files** | frontend-final.log |
| 前端类型检查 | 通过 | frontend-final.log |
| Next生产构建 | 通过，本轮新构建 | frontend-build-final.log |
| shadcn纪律 | 通过 | discipline-final.log |
| 真实三进程 | **2 passed**，包含本次补发、R1及原SC兼容 | three-process.log |
| 真实浏览器 | **7个行为场景通过**，SQLite确认仅2次新补发 | ui-final-retry.log / ui-sqlite.log |
| 旧应用门禁 | 基线v2应用实际拒绝v3 | old-app-gate.log |
| 源码一致性 | 本地与最终容器Python源码/正式测试、前端源码/依赖清单一致 | source-match.log |

全量结束后只补正了两个源码文档字符串，生产逻辑未改变；随后最终专项14项与静态检查再次通过。原审计探针仅存在容器副本，通过`--ignore=tests/test_state_reward_audit_probe.py`排除，不属于正式仓库测试。Go源码没有修改，本轮未重跑Go单元/race/vet/build；实际代理合同由上述链路验证，不借用旧测试数。新构建三进程SSE首段 **54.744 / 56.791 ms**，实际模型取消 **6.437 / 4.737 ms**。

实际命令均在既有容器中执行：

```sh
# Node容器 /work/frontend，前端全量与后端全量顺序执行
npm test
npm run typecheck
npm run build
# Python容器 /work/backend
PYTHONUNBUFFERED=1 PYTEST_XDIST_AUTO_NUM_WORKERS=2 EIDOLON_CORE_BINARY=/work/eidolon-core EIDOLON_NEXT_SERVER=/work/r2-next-standalone/server.js timeout 550 .venv/bin/pytest -q -n auto --ignore=tests/test_state_reward_audit_probe.py
.venv/bin/pytest -q tests/test_grant_schema.py tests/test_grant_idempotency.py tests/test_reset_card_grants.py
.venv/bin/ruff check src tests --extend-exclude tests/test_state_reward_audit_probe.py
.venv/bin/pyright --pythonpath /work/backend/.venv/bin/python
```

独立Standards与Spec审查均未发现生产实现P1/P2；Spec提出的3项非阻塞测试建议（取消等待/不同ID并发、多pending独立清除、关闭时新ID再开放成功）已补齐并通过全量。之后请求追加复看时，两名审查代理均因额度限制未能再运行；此失败不记为复审通过。主代理已核对新增断言、最终差异与日志，生产逻辑未再改变。本切片没有已知未解决P1/P2；旧无ID兼容限制仍明确保留。

## 资源、证据与提交边界

没有新增Docker镜像、容器、卷或宿主依赖。复用既有Python/Node容器；本次仅在Node容器新增Playwright Chromium headless shell、FFmpeg及必要运行库，浏览器缓存约 **280 MiB**。从安装浏览器前到验收结束的可写层观测：Node **1171226624→1756790784 bytes**，Python **2490355712→3176243200 bytes**，合计约 **1212.55 MiB**；这是该观测区间增量，不是整个R2总增量，也不是OrbStack虚拟磁盘净变化。

宿主新scratch约 **440 KiB**（收尾材料会少量增长），含日志、规格、合成夹具生成脚本和截图，没有在宿主安装依赖或执行构建。新Next standalone直接在容器之间复制到Python的`/work/r2-next-standalone`，原包保留。UI合成库保留在Python容器`/tmp/r2-ui-2mojyuoo/`和`/tmp/r2-ui-gxnf7846/`，浏览器截图/日志另在Node的`/tmp/r2-ui-artifacts/`及本轮scratch。

结束时停止本轮启动的既有容器，Node移除本轮临时内部网络连接以恢复原断网状态；不删除容器、缓存、临时库、系统运行库或日志。日后清理需逐目录审查本轮缓存/包/夹具，不执行prune，也不删除现有用户资源。

只提交本票相关源码、正式测试/冻结SQL夹具、ADR与本报告，排除全部scratch、日志、数据库、构建产物、.DS_Store和docs/ideas/。SHA在最终交付消息给出，避免文档自引用。只做本地提交，不推送、创建PR或部署。

## 修改范围与剩余限制

源码：gifts.py、gifts_api.py、grant_schema.py、work_schema.py；前端adminGifts、resetGrantIntents、ResetCardGrants、中文文案和Next代理头。
测试：grant_idempotency、grant_schema、冻结sc-grants-v2夹具、原迁移测试版本期望、三进程合成后端/合同、前端持久意图与代理头合同。文档：本报告、ADR-0029增量；规格/票与所有日志留在scratch不混入提交。

未实施钱包/好感/成就的作品键迁移、通话持久结算、非SC玩法或Go直连SQLite。R2只给带稳定ID的补发请求提供重试保证，旧客户端/丢失客户端ID/恢复旧备份的限制仍在。随机操作ID不按用户归属，全局保留无操作者/收件人信息；不把它误登记为可随管理员注销清除的账本。

R2之后可评估Go好感度查询读模型：通过Python私有user/work读取边界供给状态，由Go计算自然恢复与档位、保持旧GET响应；SQLite仍由Python访问。不在本轮启动。
