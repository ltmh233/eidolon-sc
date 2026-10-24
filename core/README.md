# Eidolon Go core — first slice

AGPL-3.0-or-later，沿用根目录许可证。本目录为可信作品目录和可选兼容入口，**不是完整的多作品聊天后端**。

从仓库根目录运行（Go >= 1.27，无第三方 Go 依赖）：

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
| `GET /api/works/sc/characters` | Go 通过 Python 的公开目录适配，返回 `sc:mano` 等全局 ID；`display` 完整保留旧目录字段 |
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
