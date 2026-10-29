# SC 历史会话删除：作品声明与并发404合同

2026-10-29（Asia/Shanghai，日志保留容器原时间）。分支`refactor/multi-work-dlc`，基点`af5979f0c0e2214b3b279f1d25849f3ef6332481`。本轮仅Python最小路由修复；Go DELETE迁移暂缓。规格和已接受范围在`.scratch/go-conversation-delete/spec.md`及单票。当前保留待审diff，不暂存/提交、push、PR或部署。

## 实现范围

- `backend/src/eidolon/app.py`：公共bodyless DELETE仅加`Depends(sc_request)`逐项校验header/query作品声明；仅在`store.delete_conversation`调用周围捕获具体Store.NotFound，转原404“对话不存在”。没有全局LookupError处理，不解析DELETE JSON。
- `backend/tests/test_delete_conversation_contract.py`：声明拒绝与合成业务快照、两请求均真实预读后的实际Store写事务竞争，共两项测试。跨用户/作品公开删除隔离复用三进程原场景。
- `backend/tests/test_three_process.py`：真实Next生产包→Go→FastAPI及直连Python的声明拒绝零写入、合法SC历史204/重复404/当前409。
- 本报告。没有Store/SQL排序、Schema、Go/RPC/凭据、Frontend、活动中409/自动挂断或后台任务策略变更。

省略作品或精确sc维持原行为；header/query显式非SC、非法值、重复冲突在业务写入前409。作用域依赖先于路由UserId执行，Origin仍由原中间件先检查；这收紧非法声明的拒绝优先级，与既有sc_request路由/Go网关约定一致，不改变合法旧请求。无body模型的DELETE仍不解释JSON，即使Content-Type为JSON且内容不合法也不新增解析错误。

事务内SC/用户/角色及`created_at DESC,rowid DESC`当前判定完全沿用原Store；当前409、合法历史204空体、顺序重复或竞争消失404。记忆保留、来源解绑、照片/消息/事件/搭话/通话记录删除及奖励保留均沿用原事务，不新增副作用。

## 红绿证据与测试命令

日志目录`.scratch/go-conversation-delete/logs/`，失败保留。

| 日志 | 实际结果 |
|---|---|
| contract-red.log | **2 failed、1 passed**。首个header Demo请求实际返回204并删掉SC历史，快照不一致；两个请求均完成预读、放行原Store写事务后实际为204+500。不是Mock直接抛异常，跨作品目标原404正常。红测首个声明失败即停止该循环，不声称所有声明都逐个红测。 |
| contract-green.log | 新专项3项+原删除7项，**10 passed**。修复后竞争204+404，当前及顺序404、记忆保留、迟到标题等兼容。 |
| chain-static-first.log | **12 passed**：两种素材基址完整三进程+上述10项，ruff通过；pyright发现测试params列表不变性1错误。改成保留重复query的原始查询字符串后复验，未改变请求语义或成功预期。 |
| related-static-final.log | P3复审前阶段：**177 passed、0 failed、0 skipped**，35项既有弃用警告，17.93秒；ruff通过、pyright零错误/警告。包含真实三进程、新专项及相关既有回归。不是Python全量，也不与12项或10项累加。 |
| related-reviewed-final.log | 最终去重后：**176 passed、0 failed、0 skipped**，35项既有弃用警告，17.62秒；ruff通过、pyright零错误/警告。移除重复跨作品测试，将其404详情和零副作用断言并入原三进程跨用户/作品循环；没有跳过测试或删除断言。 |

竞争测试使用实际FastAPI ASGI HTTP和合成SQLite：门控Store方法入口确保两请求的公开预读都已完成；放行后调用原绑定Store方法，走原Database.write及事务。ASGI异常转换按真实服务器方式返回500以确认红测，未把直接抛NotFound的mock当作竞争。成功判断为真实204/404响应、历史行确已删及当前行保留；清理总会放行门并取消收束未完成请求，不以清理当成功。

声明矩阵覆盖header/query非法值、双来源及重复冲突，两次提交均比较gameplay_snapshot定义的合成业务表；合法sc+未解析的坏JSON204验证bodyless协议。用户隔离和当前保护复用原删除测试；三进程同时验证网关、直连与原跨用户/跨作品目标404、错误详情及业务快照不变。会话身份约束、FK和写者没有改变。

容器`/work/backend`使用现有.venv：

```sh
.venv/bin/pytest -q tests/test_delete_conversation_contract.py
.venv/bin/pytest -q tests/test_delete_conversation_contract.py tests/test_delete_past_conversations.py
```

最终相关运行设置EIDOLON_CORE_BINARY/EIDOLON_CREATE_BINARY为`/work/eidolon-core-proxy-final`，EIDOLON_NEXT_SERVER为`/work/r2-next-standalone/server.js`，`PYTEST_XDIST_AUTO_NUM_WORKERS=2`、`PYTHONUNBUFFERED=1`，总`timeout 550`：

```sh
.venv/bin/pytest -n auto -q tests/test_call.py tests/test_health.py tests/test_three_process.py tests/test_delete_conversation_contract.py tests/test_delete_past_conversations.py tests/test_work_conversations.py tests/test_data_access.py tests/test_photos.py tests/test_photo_failures.py tests/test_conversation_titles.py tests/test_daily_tasks.py tests/test_achievements.py tests/test_achievements_stats.py tests/test_stats.py tests/test_call_stats.py tests/test_data_package_conversations.py tests/test_delete_account.py tests/test_admin_delete_user.py
.venv/bin/ruff check src tests --extend-exclude tests/test_state_reward_audit_probe.py
.venv/bin/pyright --pythonpath /work/backend/.venv/bin/python
```

每轮使用独立`/work/delete-*-20261029`合成临时目录。宿主机只运行`affected-tests.sh --list af5979f0`读取清单，实际依赖/测试/静态均在Docker。

## 未验项与边界

按协调收窄后的要求，未重建完整生命周期框架。未单独为本次路由修改重新注入“解绑后部分SQL失败”、向量字段逐列比较、所有新建/导入/写回/注销交错、提交后丢响应或入队取消；底层代码未变，复用了原删除、DataAccess回滚、迟到记忆/标题/照片及隔离/奖励回归。原活动历史只读语义差距未修复，不新增活动检测/挂断策略。

没有运行Python全部测试，也未重复Go unit/race/vet/build或前端全量/类型检查/构建；Go/Frontend无改动，使用已验收的固定二进制/生产包执行真实链路，未把历史验收写成本轮新执行。没有生产数据、安全磁盘擦除/备份或生产容量许可。

## 下一项仅推荐（未实施）

推荐Go历史/摘要展示文字的**交付提示净化纯模型**：当前Python`delivery_cues.CueFilter/without_cues`仍为Go历史与摘要提供已过滤文本。把各类括号/成对标签、跨分段等待、80字符边界、Unicode空白及收尾规则真正计算到Go公开文本投影，可由现有Python实现做差分基线，集中到Go读模型复用。它有实际状态机和隐私可见性规则，不是改名转发；不依赖可变SQLite事务判定，也不需要新增写权限。Python仍保留数据库、模型与音频生成。这里只提出这一项，未写下一票、实现或扩大当前范围。

## 独立复审与最终核对

Standards轴最初指出一项P3重复测试；按上述方式合并后复审无finding，原断言及循环场景标识保留。Spec轴无finding，确认真实竞争窗口和原Store写事务已命中。两轴均未发现未解决的P1/P2；复审只覆盖本票范围，不等于完整生命周期审计。

`source-match-reviewed-final.log`确认最终宿主源码/测试与容器副本一致（排除缓存及容器旧审计探针）；受测app.py SHA256为`cd8ae5826ef1ad3ff053dddd5a7104e1acfc698a4b4faa6be78c73d695a6b2cb`。Go二进制`d4573ba8defb689e306b1872cff5adeec4e2da0945427b48b2ddf72f95b2bf94`、Next server.js `3a44d862ae978d8ebe4195c385c5820e04ba413be37d6e5ebe75ffa4a3a7227c`和已编译代理route.js `0113206246f62f07f1c31eacf30883c5f55ac1c8152ececadf0883bd4d596eae`为复用包，并非本轮新构建。

Docker仅复用`eidolon-accept-python-20261024`，只读挂载项目到`/source`，无宿主端口发布。本票初始容器可写层3,285,864,448字节，最终3,450,949,632字节，增加165,085,184字节（约157.44 MiB，含临时合成库和测试缓存；非宿主物理磁盘精确增量）。没有新增镜像、容器、卷或依赖。宿主新增本报告、正式测试以及`.scratch/go-conversation-delete/`规格、票和日志（收尾前约72 KiB）；另修改app.py和已有三进程测试。未清理任何文件或Docker资源。后续如需清理，应先审查并备份日志，再仅移除本票明确的`/work/delete-*-20261029`测试目录；本轮没有执行。

最终保留两处已跟踪修改、两份待审新文件，无暂存或提交；HEAD仍为`af5979f0c0e2214b3b279f1d25849f3ef6332481`。原未跟踪资料保留。本票可交维护者审查，不代表批准Go DELETE迁移、生产部署或下一个实施切片。

收尾已停止本票启动的既有Python验收容器（`container-stopped.log`：exited），保留所有容器内容和日志，其他容器未操作。`git diff --check`通过，暂存区为空。

## 2026-10-29 接受与本地提交收尾

协调复核已逐项阅读完整差异、新测试、红/绿日志并核对三份源码哈希及容器记录，接受本轮176项和静态检查证据；该复核没有重新运行测试。独立Standards/Spec均无finding。现授权仅将上述四个交付文件本地提交；此前“未提交”描述保留为当时状态，scratch、日志和其他未跟踪资料不入提交。提交SHA及精确清单另记`.scratch/go-conversation-delete/local-commit-receipt.md`，避免在提交自身记录自引用SHA。不push、PR或部署。下一项仅准备Go展示文字过滤的规格和单票，尚未实施。
