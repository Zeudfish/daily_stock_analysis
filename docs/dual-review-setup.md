# A 股双项目复盘工作流配置

工作流：`.github/workflows/a-share-dual-review.yml`

每天工作日北京时间 18:20 运行上游项目：
- `ZhuLinsen/daily_stock_analysis`：运行大盘复盘模式。
- `simonlin1212/vibe-astock`：启动上游 Orchestrator API，执行短线情绪 AI 复盘。

两个项目的原始 Markdown 报告作为邮件附件发送到 `zeurdfish@gmail.com`。GitHub Actions 产物保留 30 天。项目源码在每次运行时从上游浅克隆，不复制或改写上游代码。

## 需要添加的 GitHub Actions Secrets

进入仓库 **Settings → Secrets and variables → Actions → New repository secret**。

| Secret | 用途 | 必需 |
|---|---|---|
| `GMAIL_APP_PASSWORD` | `zeurdfish@gmail.com` 的 Google 应用专用密码（不是 Gmail 登录密码） | 是 |
| `DEEPSEEK_API_KEY` | Vibe AStock 的 DeepSeek 模型 API Key | 是 |
| `MODELFLARE_KEY` 或 `AIHUBMIX_KEY` 或 `GEMINI_API_KEY` 或 `OPENAI_API_KEY` | DSA 的模型 API Key，四选一即可 | 是 |
| `TUSHARE_TOKEN` | DSA 行情数据增强 | 否 |
| `GMAIL_SENDER` | 发件邮箱；默认 `zeurdfish@gmail.com` | 否 |

如果使用 OpenAI 兼容服务，可另外添加 Repository Variables：
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`

Gmail SMTP 使用 `smtp.gmail.com:465`。需要先为 Google 账号启用两步验证，再生成应用专用密码；不要把 Gmail 登录密码放进 Secret。

## 运行与验收

1. 添加上表密钥。
2. 打开 Actions，启用工作流。
3. 选择 **A-share dual project review** → **Run workflow**。
4. 成功验收需同时看到 DSA 与 Vibe 两份报告附件，并收到主题为 `[A股项目复盘] [YYYY-MM-DD] 完整` 的邮件。
5. 若只有一个项目成功，邮件主题会标为“部分完成”；缺失项目不会由脚本自行搜索行情代写。

手动运行会使用 Vibe 自动解析的最近一个已完成交易日；工作日定时任务遇到非交易日会跳过，不把上一交易日冒充为当天。

## 数据与安全

- Vibe 的 `.local` 历史目录通过 GitHub Actions Cache 跨运行恢复和更新；第一次运行仍没有此前历史，短线历史样本可能不完整。缓存中不保存 API token 文件。
- 所有 API Key 和 Gmail 应用专用密码仅从 GitHub Secrets 注入，不写入仓库。
- 邮件正文会包含交易日、两个项目的状态、上游 commit SHA 和 Actions 运行链接。
