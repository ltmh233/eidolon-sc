# 第三阶段 R1：旧 SC 玩法接口作品边界修复

日期：2026-10-27。基线 `97fef3f55212c368b6dfa86880f638b09f3ac077`，分支 `refactor/multi-work-dlc`。本轮仅修复显式作品声明落入 SC 玩法的问题；不是新的玩法或数据库迁移。原始审计保留于 `.scratch/state-reward-audit-20261027/audit.md`。

## 实际基线与范围

开工时 HEAD 与审计基线一致，暂存区和已跟踪文件无改动。已有 `.scratch/`（包括新出现的 `.scratch/.DS_Store`）、`docs/ideas/`、`docs/.DS_Store` 全部保留。未访问真实 `.env`、生产库、东京 SoftBank 或 Azure。

实际链路为 FastAPI route → Gifts/Happenings/Affinities → 单写线程 SQLite 事务。`GET /api/gifts` 会经 `Gifts.state` 初始化余额或补发当日抽奖，因而只保护 POST 不够。旧礼物/抽奖 Pydantic 模型会忽略未知的 work_id；Go 对旧路由检查 header/query，但仅对创建会话和 offer 检查 JSON，所以礼物 JSON 会透传并操作 SC。

## 修改与拒绝时点

- `backend/src/eidolon/sc_scope.py`：`require_sc` 仅接受精确字符串 `sc`；`sc_request` 检查每个 `X-Work-ID` 和每个 query `work_id`，包括重复值。任何一个值非法即 HTTP **409**，detail 为既有 `legacy API supports sc only`，没有内部路径、SQL 或用户信息。
- `gifts_api.py`：用带统一依赖的局部 APIRouter 注册原路径；DrawInput/GiftInput 增加可选 `work_id: object = "sc"`，两个 handler 第一项业务动作即调用 require_sc。保留 object 是为了在 handler 明确拒绝 null、布尔、数字、数组和对象，而不是被模型转换为字符串或静默默认。其他未知字段继续按原规则忽略。
- `app.py`：角色好感读取路由安装相同依赖。
- `stats.py`：读取好感/成就的用户统计路由安装相同依赖。
- `test_sc_play_scope.py`：HTTP 输入矩阵、真实 SQLite 前后比较、实际 Go/Uvicorn 网关、合法 SC 对照。
- `test_three_process.py`：在已有 Next 生产入口 → Go → Uvicorn 测试中增加玩法拒绝与 SQLite 比较，并与直连 Python 对照；原认证、历史迁移、记忆召回、SSE 取消断言保留。

Header/query 在 route 业务执行之前通过依赖拒绝；JSON 使用 FastAPI 已解析的请求模型，在调用 Happenings/Gifts 前拒绝。认证/会话仍沿用既有机制，可能续期会话 Cookie；“零副作用”针对业务持久化，不要求取消正常认证流程。没有先扣资源再补偿或依赖回滚来实现拒绝。

## 路由与实际声明渠道

| 原有路径 | 方法 | 作品声明渠道 |
|---|---|---|
| /api/gifts/catalog | GET | header/query |
| /api/gifts | GET | header/query；拒绝在初始化/每日补发前 |
| /api/gifts/draws | POST | 原 JSON body 顶层 work_id + header/query |
| /api/characters/{id}/gifts | POST | 原 JSON body 顶层 work_id + header/query |
| /api/gifts/resets | POST | header/query；保持无 body 参数协议 |
| /api/characters/{id}/achievements | GET | header/query |
| /api/characters/{id}/affinity | GET | header/query |
| /api/stats | GET | header/query |
| /api/admin/gifts | GET | header/query |
| /api/admin/gifts/reset-card-grants | POST | header/query；保持无 body 参数协议 |

没有增加 GET JSON 协议；重置卡与管理员补发仍不读取/解释请求体。客户端在这些无 body 参数路由附加 JSON，并不成为服务支持的作品声明渠道；应使用 header/query。该限制按本轮“不改变原本不接受 JSON body 的接口协议”的要求保留，不能宣称扫描了所有任意 HTTP body。

SC 玩法的上述公开入口均覆盖。账号/管理员配置（包括全局 affinity 开关）、OAuth、WebRTC、流式聊天、文件上传、记忆及会话接口没有安装本轮依赖；它们沿用原边界，本轮不宣称完成整个旧 API 的统一作品防护。尤其直连 Python 的其他旧 SC 路由，不应依据本报告推定已经具备 Go 的所有 header/query 检查。

## Go 与 Python 的职责

Go **没有修改**：既有 header/query 检查会先拒绝非 SC；礼物/抽奖 body 原样转发，由 Python 的正式 handler 检查。Python 直连拥有同样的玩法安全边界，不能只依赖网关。真实网关专项验证 body 的 demo/null/非法类型不能借合法 SC header/query 覆盖。

因此未新增 Go body 缓冲、大小上限或错误读取分支，也不引入两种 JSON 解析语义。既有网关对 conversations/offer 的有限 body 处理保持。未修改 Go、Next 源码，本轮无需为源码变化重新编译它们；集成复用上一轮容器内已有 Go 二进制和 Next 生产 standalone，明确区别于本轮重新构建。

缺省作品按旧 SC 执行；合法 sc 可与缺省混用、可重复声明 sc。任一来源为 demo/未知/空串/大小写不符/空白/非法 JSON 类型/null 都拒绝，无来源优先级。HTTP header 名大小写由协议处理；值不作大小写折叠或业务层 trim。真实网络会按协议处理 header OWS，HTTPX 在发送 `" sc "` 时已拒绝；测试明确区分此客户端失败与 ASGI 中未经规范化的 header 被服务拒绝，不把前者记为网关拒绝证据。

## 先失败、后修复及独立复查

证据均保存于 `.scratch/sc-play-scope-20261027/`，不提交日志。

| 日志 | 实际结果与解释 |
|---|---|
| body-red.log | 11 failed：其中9个非法body场景实际写入SC库存、余额/补发水位、会话、任务和成就；另2个合法对照已成功送礼，但测试误用消耗完库存后的字典下标而KeyError。改为按背包既有省略零库存语义读取，未放宽资源消费断言。 |
| body-green.log | 11 passed，最小body修复通过。 |
| declarations-red.log | 110 failed、29 passed；10条路由×11种header/query声明明确复现未保护入口。 |
| declarations-green.log | 139 passed；直连玩法矩阵修复通过。 |
| integration.log | 141 passed、1 failed；两组三进程通过，网关矩阵因HTTPX不能发送首尾空白header而失败。随后分别验证客户端拒绝和未规范化ASGI值拒绝。 |
| full.log | 2246 passed、1 failed、1 skipped；网关body矩阵得到401，Cookie仍绑定原ASGI域，未实际认证。修复测试Cookie域传递，保留合法SC消费对照，避免把认证拒绝误当作品校验通过。 |
| static-first.log / static.log | 初始工具调用未指定虚拟环境解释器、以及 --exclude 覆盖默认排除导致扫描.venv；属于检查命令问题，保留输出。后改显式 src/tests 范围和 --pythonpath。 |
| static-retry2.log | ruff通过，pyright3项测试类型错误（transport.app、Cookie可空类型）；增加运行时transport类型确认并排除无值Cookie，业务代码未更改。 |
| integration-retry2.log | 最终测试结构下19 passed，含17项玩法专项和2组三进程；随后仅修正测试类型信息，再运行全量。 |

中途另两份未执行日志 `integration-final.log/static-retry.log` 明确记录容器停止；重启同一容器后复验，未将其记为测试失败或通过。

独立 Standards 审查提出1项P3：同一拒绝分支的参数变体应改为具场景标识的循环。已经保留10条路由接缝，将非法值与来源变体折叠到循环，原SQLite及状态码断言全部保留。Spec 审查建议补全新用户拒绝不初始化余额、显式SC抽奖成功，均已加入。最终两项独立复核无未解决规范问题或新增P1/P2。测试数量因规范整理减少，不代表删去输入场景。

## 数据与兼容性验证

拒绝前后逐表比较：gift_balances（含given_on）、gift_backpacks、gift_rewards、gifts_opened、reset_card_grants、affinities（含cooling/gained字段）、daily_task_progress、achievement_progress、achievement_stages、achievements_launched、conversations、messages、conversation_events、calls。种子状态包含真实抽奖产生的库存、合法赠礼产生的任务/奖励、管理员补发得到的重置卡和冷却中的角色；时间前进制造待补发。非法请求重复2次仍不改变数据，另有全新用户缺余额的非法GET对照。

合法对照包括缺省和显式SC各连续送出两份礼物，库存1→0；direct/gateway显式SC抽奖实际消费次数并得到cake；未知旧JSON字段继续接受。网关重复query sc可正常执行。原有礼物、好感、任务、成就、管理员、记忆、数据包、注销与事务行为由最终Python全量覆盖。

数据库 user_version、DDL、唯一键、触发器及迁移源码均未修改。返回JSON格式不变，无新增必填字段；契约变化仅为已有SC玩法接口开始识别可选work_id，并明确拒绝非法作品声明。无非SC玩法开放、钱包迁移或新幂等协议。

## 最终结果（复验结束后追加）

最终全量 **2124 passed、1 skipped、117 warnings**，114.30秒；唯一跳过是Windows proactor专用测试。证据为 `full-final.log`，新测试折叠矩阵后共17项，输入场景与逐表断言保留。最终ruff通过；pyright **0 errors、0 warnings**，见 `static-final.log`。全量包含2组真实三进程及Go/Python玩法专项，不借用历史通过数。

最终源码对应的独立集成 `integration-retry2.log` 为 **19 passed**（17项专项+2组三进程），随后仅增加测试类型确认后通过上述最终全量。该次两组三进程SSE首段和取消延迟详见日志；未更改流处理代码或观察窗口。

执行命令（容器内，工作目录/work/backend）：

```sh
EIDOLON_CORE_BINARY=/work/eidolon-core EIDOLON_NEXT_SERVER=/work/next-standalone/server.js .venv/bin/pytest -q -s tests/test_sc_play_scope.py tests/test_three_process.py
PYTHONUNBUFFERED=1 PYTEST_XDIST_AUTO_NUM_WORKERS=2 EIDOLON_CORE_BINARY=/work/eidolon-core EIDOLON_NEXT_SERVER=/work/next-standalone/server.js timeout 550 .venv/bin/pytest -q -n auto --ignore=tests/test_state_reward_audit_probe.py
.venv/bin/ruff check src tests --extend-exclude tests/test_state_reward_audit_probe.py
.venv/bin/pyright --pythonpath /work/backend/.venv/bin/python
```

仓库受影响测试脚本以 `--list 97fef3f5` 核对，列出的call、health、scope及three_process全部包含在实际全量中；没有在宿主机执行依赖测试。Go源码未修改，本轮未重新跑Go单元/race/vet/build；前端未修改，本轮未重跑前端单元/类型检查/构建。它们的旧日志不混入本轮结果。

本切片无未解决P1/P2；既有R2作为明确排除范围保留，不能据此声称全部奖励幂等风险已消除。

## 剩余范围与资源

R2管理员补发请求幂等机制仍是已知、明确排除的后续工作；本轮没有修复或降低其风险。角色/钱包/成就作品迁移、通话持久结算、单作品清理、其他非SC运行时仍未实现。R1验收通过后可以单独转向R2，但需要定义同一次操作的稳定身份和合法新补发的区别，不能直接以日期限制重复发放。

复用既有Python容器；没有新增镜像、容器、数据卷、端口或宿主机依赖。只挂载项目目录，容器内使用合成认证、模型和测试数据库。保留日志、pytest临时库和原审计探针；原探针 `tests/test_state_reward_audit_probe.py` 只存在容器副本，故意断言旧缺陷，最终全量通过显式 --ignore 排除它，ruff也仅对这一非仓库诊断文件排除；没有跳过正式失败测试。没有删除scratch或用户文件。

允许的提交仅包含本报告、4个Python源码文件及2个正式测试文件。提交SHA见最终交付消息，避免本报告自引用；禁止推送、PR和生产操作。

收尾源码比对 `source-match.log` 确认本地源码及正式测试与通过全量的容器副本完全一致（排除__pycache__和旧审计探针）。独立集成的SSE首段 **48.578 / 51.219 ms**、实际模型取消 **5.379 / 5.712 ms**。本轮宿主机scratch约 **9.7 MiB**，大部分来自保留的首次静态检查误扫描日志；未量测容器层净增长，不宣称零额外磁盘占用。测试后停止本轮启动的既有容器，保留所有数据及资源。
