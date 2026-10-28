# Eidolon Go core — first slice

第二阶段支持世界 v1、角色/世界 v2，以及加 `-with-gifts` 导出的角色/世界/礼物 v3 私有快照。Python 通过 `EIDOLON_CHARACTER_SNAPSHOT` 消费 v2/v3。Go 的 `-content-snapshot` 提供基础静态目录；同时配置 `-catalog-display` 后可独立提供完整 SC 展示目录。展示元数据由离线 Python 命令复用旧接口算法生成，不需要运行中的 Python 服务。旧 `/api/characters` 仍由 Python 提供。详见 [世界切片](../docs/migration/go-content-world-slice.md)、[角色切片](../docs/migration/go-content-character-slice.md) 和 [收尾验收](../docs/migration/go-content-final-slice.md)。

AGPL-3.0-or-later，沿用根目录许可证。本目录为可信作品目录和可选兼容入口，**不是完整的多作品聊天后端**。

从仓库根目录运行（Go >= 1.27；TOML 使用锁定的 `github.com/pelletier/go-toml/v2 v2.2.4`，MIT 许可证，校验和在 go.sum）：

```sh
cd core
go test -race ./...
go vet ./...
go build -trimpath -o /tmp/eidolon-core ./cmd/eidolon-core
/tmp/eidolon-core -packs packs,testdata/packs -legacy http://127.0.0.1:8000
```

将本地 Next.js 的 `BACKEND_URL` 设为 `http://127.0.0.1:8081` 并重启前端。旧 SC 界面无需修改；Go 不依赖前端构建期配置。Python 必须保持原来的独立监听地址，`-legacy` 不能指向 Go 自己。

也可在本地用仓库根目录的 `docker-compose.dlc.yml` 叠加 `docker-compose.yml`。不要与生产 Compose 混用；原生产文件未修改。测试包只在显式传入 `testdata/packs` 时加载，默认构建镜像不包含它。

| API | 行为 |
|---|---|
| `GET /api/works` | 默认作品、作品名称、能力声明 |
| `GET /api/works/sc/characters` | 配置展示元数据时由 Go 原生提供；否则保留 Python 公开目录适配。返回 `sc:mano` 等全局 ID，`display` 保留旧目录字段 |
| `GET /api/works/demo/characters` | 完全由 Go 从内容包读取；不公开人设 |
| `GET /api/works/demo/world` | 该作品单独的世界设定；此字段是公开内容 |
| `GET /api/works/sc/world` | 501；SC 世界设定继续由 Python 内部使用 |
| `GET /api/works/demo/rag` 等 | 501；尚未接入运行时，无 SC 回退 |
| `POST /api/works/demo/conversations` 等 | 405；本阶段仅目录读取 |
| 原 `/api/*` | 流式转发 Python，原有认证、Origin、Cookie、错误码、头像 URL 保留 |

新 API 不把裸角色 ID 当全局身份。`display.id` / `local_id` 是显示或兼容元数据，不得拿 `demo` 的 `local_id` 调旧 SC 聊天接口。旧路由省略作品作用域表示 SC；query/header 显式指定非 SC 会返回 409。创建对话和 offer JSON 中显式的 `work_id`（含 offer 的 `requestData`）也会检查；这两种请求上限 1 MiB。文字回合 JSON 的 `work_id` 由 Python 输入边界检查，省略或 `sc` 照常执行，其他值返回 409，发生在调用模型、扣额度和写历史之前。Go 不为文字回合新增请求缓冲或大小限制，数据包导入等大请求仍流式转发。Python 直连端口仍需保持私网。

内容包格式见 `testdata/packs/demo/work.json`。schema v1 支持 `schema_version`、`work_id`、`name`、`source`、`world`、`characters`；每位角色有 `id`、`name`、`intro`、`sheet`。新作品 `source` 必须为 `content`。ID 限小写字母、数字、下划线与短横线，重复作品、同作品重复角色、缺少人设/世界设定、未知字段或不支持的 schema 均使启动失败。每包最多 2 MiB。目录按作品 ID 排序；角色保持包内顺序。重启时加载快照，无热重载；内容包属于可信部署输入，不能让用户上传任意目录。

接入学偶：创建 `my-packs/gakumas/work.json`，填入审核过的名称、世界设定和角色，运行时把 `my-packs` 加入 `-packs`。可直接浏览目录，无需修改 Go 业务代码。SC 必须继续从 `packs/sc` 注册。**这还不能与学偶角色聊天**；人设、音色、素材授权、RAG 数据导入和运行时作用域是后续工作。

完整 TCP 与真实 FastAPI 联调（需要 Python 项目依赖和本机监听权限）：

```sh
EIDOLON_TCP_TESTS=1 go test -race ./...
go build -o /tmp/eidolon-core ./cmd/eidolon-core
cd ../backend
EIDOLON_CORE_BINARY=/tmp/eidolon-core uv run pytest -q tests/test_go_catalog.py tests/test_characters.py
```

CI 已配置以上检查。没有 `EIDOLON_CORE_BINARY` 时 Python 联调明确 skip；没有 `EIDOLON_TCP_TESTS=1` 时只跳过真实 TCP 测试，HTTP framing/SSE 的内存连接测试仍执行。

回滚：恢复 Next.js 的 `BACKEND_URL` 指向 Python 并重启前端，停掉 Go。没有数据库回滚步骤。当前 Docker overlay 的 `GOMEMLIMIT=64MiB` 是 GC 软目标，96 MiB 容器限额是试验预算，不是已测得峰值；应在上线前连同并发 SSE/语音做总 RSS 测量。提供修改版网络服务和分发镜像时，应保留 AGPL 声明并提供对应版本的完整源代码；内容素材权利与代码许可证分别处理。

## 内容发布（在准备好的隔离构建环境中运行）

每次使用新的发布目录，下面路径仅为示例。先编辑或生成源内容，再一次性导出；不得直接修改正在运行服务所挂载的内容目录。

```sh
/work/eidolon-core -packs /work/core/packs,/work/core/testdata/packs \
  -sc-world /work/characters/world.json -sc-characters /work/characters/characters.toml \
  -with-gifts -export-worlds /release/content.json
cd /work/backend
.venv/bin/python -m eidolon.content_publish \
  --snapshot /release/content.json --characters /work/characters/characters.toml \
  --output /release/display.json
```

有外部素材域名时，加 `--assets-url`，与 Python 的素材基址配置一致。此命令不生成或上传音频，只调用原有纯展示算法。源内容、素材、两个输出应作为同一不可变版本部署：Go 配置 `-content-snapshot /release/content.json -catalog-display /release/display.json`；Python 配置 `EIDOLON_CHARACTER_SNAPSHOT=/release/content.json` 和 `EIDOLON_CATALOG_DISPLAY=/release/display.json`。后者在创建数据库前验证目录与内容摘要、完整 JSON 和素材配置一致。先校验，再重启两个服务并对比完整目录；不支持热更新。

每个输出单独原子替换，**两个文件不是一个跨文件事务**。不得混用发布版本；Go 检查摘要绑定和公开字段白名单，Python 再检查完整展示投影。两个服务各自配置不同但完整合法的发布版本仍需发布流程/上线前比对阻止，不能依靠本地摘要检查发现。

v1 世界路径保持兼容；省略 `-with-gifts` 导出 v2，Python 从旧 gifts.toml 加载礼物供回滚。旧消费者不接受 v3，需同时回滚消费者和同批 v2 源内容。完全回滚可撤销快照/展示配置并恢复原加载方式；数据库无需回滚。显式配置损坏快照时禁止静默回退。

真实三进程验收需准备 Next 生产 standalone 构建与 Go 二进制，在测试容器内运行：

```sh
cd /work/backend
EIDOLON_CORE_BINARY=/work/eidolon-core \
EIDOLON_NEXT_SERVER=/work/next-standalone/server.js \
.venv/bin/pytest -q -s tests/test_three_process.py tests/test_content_release.py
```

测试分别启动 Next、Go、Uvicorn，使用临时 SQLite，仅模拟外部服务。未设置两个二进制路径时三进程测试明确跳过；当前 CI 不等于已自动运行这项生产构建验收。

## SC affinity read model (optional, private upstream)

Configure Python `EIDOLON_CORE_SERVICE_TOKEN_FILE` and Go `-affinity-token-file` with purpose-limited credential files containing the same 64 lowercase hex characters (32 random bytes), readable only by the owner. This is separate from user credentials and `EIDOLON_SECRET_KEY`. Both files must be regular files with no group/other permissions. Tests create synthetic credentials; enabling this in deployment requires a separate reviewed configuration change.

With the flag, Go owns recovery, tier and cooldown calculation for `GET /api/characters/{id}/affinity`. Python authenticates the original session Cookie, fixes scope to SC, checks the character and live switch, and reads raw state from SQLite. A private versioned snapshot includes one server observation time; Go returns only the old public status shape. The Python public route remains available for direct compatibility/rollback. Without the flag, the gateway explicitly retains legacy proxy mode; once configured, upstream/contract errors fail closed without fallback.

`/api/_core` is always blocked by the public Go router. Python's hidden raw endpoint additionally requires the service credential and a valid user session; client `user_id` declarations never choose the reader. Keep the upstream on the existing private HTTP/loopback network; never expose or log service credential content. Authentication errors and Set-Cookie are preserved; internal schema/credential failures return a generic gateway error.

The contract's version covers the SC calculation rules as well as its field layout. Rule changes must update the Go/Python differential tests and consider a contract version increment; do not silently mix different rule releases. Schema remains v3 and Python remains the only SQLite accessor/writer. Gifts, stats and other affinity readers/writers remain Python-owned in this slice.

### Optional Go statistics projection

`-stats-token-file` separately enables native `GET /api/stats`, using the same owner-only core credential format and Python `EIDOLON_CORE_SERVICE_TOKEN_FILE`. The credential now authorizes **both private affinity and statistics reads**; it remains separate from user keys. The loader's old `affinity_service_auth` Python name remains an alias, while new consumers use `core_service_auth`. Configure only the desired Go read-model flags; absent flags keep their legacy proxy mode. Configured failures never fall back.

Python validates the original Cookie, SC scope and range, resolves profile/device/default timezone, reads SQLite, and supplies the existing read-only achievement tally. Go computes all date/range/hour/visit/call/gift statistics and affinity groups. The private `/api/_core/stats` stream contains only metadata, not conversation text or memories. It uses bounded NDJSON records and a mandatory terminator, with no total-history byte cap or whole-response timeout; downstream cancellation closes the private request. Go retains a fixed civil-date bitset and counts per distinct character, rather than an event list. Python still materializes the existing Store lists and performs separate reads, so this is not a new transaction snapshot or a claim of constant Python memory. Deployment and production capacity approval remain separate; see [acceptance](../docs/migration/go-stats-read-model.md).

### Optional Go conversation history projection

`-timeline-token-file` enables the original `GET /api/conversations/{id}/messages` through Go. The same core credential now permits private **affinity, statistics and conversation-history** access; unlike stats metadata, history contains sensitive chat text. Python first validates the session, SC ownership and current character catalog, reads the original record groups and captures the seen reach-out ID, then performs the existing bounded seen write. Go merges and projects the groups; Python still filters delivery cues and supplies trusted gift names. Direct Python and unconfigured gateway behavior remain available for rollback.

The private request remains GET to preserve Origin behavior. Its dedicated HTTP/1 transport disables connection reuse and HTTP/2 so a reused-connection failure cannot automatically read/mark a newer watermark. No application retries or client idempotency markers are forwarded. An authorized read can still mark the captured reach-outs even if response delivery later fails, matching the existing endpoint; this is not a new receipt protocol.

Go validates the complete stream before publishing 200. Long messages have no new 64KiB cap: request-private temporary files buffer authenticated ciphertext using in-memory, per-file random AES-256-GCM keys. Files are unlinked immediately; disk blocks do not contain readable chat copies. Keys are not persisted, but this does not promise RAM zeroization or protection of memory dumps/snapshots. Retained memory includes record/chunk indexes and the largest record; Python still materializes its original lists. Disk/CPU capacity needs separate production review. See [acceptance](../docs/migration/go-timeline-read-model.md).

### Optional Go conversation list/latest summaries

`-summaries-token-file` enables native projection of `GET /api/conversations?character_id=...` and `GET /api/conversations/latest`, using the existing core credential. Its permission now includes these authenticated SC summary candidates as well as affinity, statistics and history. Go selects among the indexed last-message/event/reach-out candidates and builds the public previews. Python keeps the original user/SC queries, conversation order and per-character latest selection, title workers and cue filtering. The list still waits up to the existing five seconds for that user's pending titles; latest does not wait. Neither endpoint marks reach-outs seen or starts titles.

One private stream carries the candidates. The existing per-conversation indexed SQL probes remain; this does not claim to eliminate their SQL N+1, and adds no per-conversation HTTP or whole-message-history scan. Unknown/removed character behavior and original public fields stay compatible. Complete output uses the existing encrypted request spool, with no new small row/history cap. Missing parameters retain the original 422 shape, while unexpected private fields fail closed.

Comparison metadata uses Python's original ISO parser and exact microseconds, preserving the original public timestamp string. The related history fix negotiates `X-Core-History-Comparison: python-us-v1`; new history consumers require the matching response capability and keys, older consumers receive their original private shape. Configure matched service versions; a mismatch returns 502. See [summary acceptance](../docs/migration/go-conversation-summaries.md). No production enablement is implied.
