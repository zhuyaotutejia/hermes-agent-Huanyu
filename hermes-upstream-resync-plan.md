# Hermes Fork ↔ 上游 v0.20.1 整合方案（方案文档，未执行）

> 生成时间：2026-08-14 · 工作仓库：`C:\work\Huanyu Hub\Hermes source\hermes-agent`（fork）· 上游参考：`C:\Note\hermes-agent`（官方 v0.20.1 clone）

## 0. 结论先行

**不要做整体 merge/rebase。** 这是一个深度自定义 fork，与官方完全分叉（fork HEAD `e6c314ee7` 在官方仓库里是 `bad object`，无共同近期祖先），核心文件差异巨大（`gateway/run.py` +8347 行、`conversation_loop.py` +2534 行、`run_agent.py` +2412 行、`chat_completion_helpers.py` +1703 行）。整体合并会引发海量冲突，且 `git fetch upstream` 都会超时。

**推荐策略：按需特性移植（cherry-pick / 手动对齐），逐个验证**，保留 fork 所有自定义。先把当前未提交改动落盘。

---

## 1. 现状盘点

### 1.1 仓库身份
- fork remote：`origin = https://github.com/zhuyaotutejia/hermes-agent-Huanyu.git`
- 上游 remote：`upstream = https://github.com/NousResearch/hermes-agent.git`（已配置，但本地快照 `d33becd87` 过时）
- fork HEAD：`e6c314ee7 fix(feishu): add SSL retry to lark_oapi Transport.execute`
- 官方 v0.20.1 HEAD：`f52feed1e fix(azure-foundry): scope Responses reasoning suppression to post-tool turns`
- **亲缘关系：完全分叉**（fork HEAD 在官方不存在）

### 1.2 未提交改动（必须先处理）
```
 M agent/agent_runtime_helpers.py   ← 非本次（fork 既存）
 M agent/retry_utils.py             ← 本次：Z.AI 中国端点修复
 M tests/test_retry_utils.py        ← 本次：对应测试
 M tools/memory_tool.py             ← 非本次（fork 既存）
?? CLAUDE.md                        ← 未跟踪
```

### 1.3 核心文件差异规模（fork 行数 vs v0.20.1 行数）
| 文件 | fork | v0.20.1 | 差值 | 冲突风险 |
|---|---|---|---|---|
| gateway/run.py | 20718 | 29065 | **+8347** | 极高 |
| agent/conversation_loop.py | 5312 | 7846 | **+2534** | 极高 |
| run_agent.py | 6013 | 8425 | **+2412** | 极高 |
| agent/chat_completion_helpers.py | 3021 | 4724 | **+1703** | 极高 |
| agent/anthropic_adapter.py | 2789 | 3216 | +427 | 高 |
| agent/error_classifier.py | 1598 | 1905 | +307 | 中 |
| model_tools.py | 1374 | 1617 | +243 | 中 |
| plugins/platforms/feishu/adapter.py | 5702 | 5895 | +193 | **高（含 fork 专有）** |
| agent/retry_utils.py | 158 | 208 | +50 | 低 |
| plugins/model-providers/zai/__init__.py | 127 | 127 | 0 | 无 |

### 1.4 fork 专有 / 必须保留的自定义
| 自定义 | 位置 | 上游是否有 | 处理 |
|---|---|---|---|
| **飞书 lark Transport.execute SSL 重试补丁** | `plugins/platforms/feishu/adapter.py:119-157` | **无**（v0.20.1 没有） | 必须 100% 保留 fork 版 |
| Z.AI 中国端点过载识别 | `agent/retry_utils.py`（本次改） | 无（上游同样有 bug） | 保留本次修复 |
| agent_runtime_helpers 改动 | `agent/agent_runtime_helpers.py`（未提交） | — | 保留 |
| memory_tool 改动 | `tools/memory_tool.py`（未提交） | — | 保留 |
| Huanyu 品牌化 / 配置 / skills | `~/.hermes/*` + 部分代码 | — | 保留 |

---

## 2. v0.20.1 值得拿的特性（按价值排序）

| 特性 | 来源 | 价值 | 移植难度 |
|---|---|---|---|
| **per-model reasoning_effort 覆盖** | `chat_completion_helpers.py:2284` | 高（你明确要的"按模型优化"） | 中（需对齐核心调用链） |
| `parse_retry_after_seconds` 集中化 | `retry_utils.py:38-87` | 中（Retry-After 解析更稳） | 低（独立函数） |
| 各种 bugfix（azure/desktop/voice 等） | 多文件 | 低-中 | 高（分散，需逐个 cherry-pick） |

> 上游**没有**的：MiniMax 专属重试、按主模型 fallback、Z.AI 中国端点修复。这些 fork 侧已自行解决或确认无需。

---

## 3. 推荐执行步骤（人工把舵，分批）

### 阶段 A：保命（先做，零风险）
1. **提交当前未提交改动**到 fork（在 main 或新分支）：
   ```
   git add agent/retry_utils.py tests/test_retry_utils.py
   git commit -m "fix(retry): cover Z.AI China coding endpoint overload (open.bigmodel.cn)"
   # agent_runtime_helpers.py / memory_tool.py 按你的意愿单独提交
   ```
2. **打备份 tag**：`git tag backup-pre-resync-$(date +%F)` 并 `git push origin --tags`
3. **新建工作分支**（绝不在 main 上整合）：`git checkout -b resync/v0.20.1-pick`

### 阶段 B：刷新上游（解决 fetch 超时）
1. 浅 fetch + 只取单分支，避免全历史超时：
   ```
   git fetch upstream main --depth=500
   ```
   或直接以 `C:\Note\hermes-agent`（v0.20.1）作为只读参考做文件级 diff，不走 git merge。

### 阶段 C：逐特性移植（每个独立验证）
对每个目标特性，**手动对齐**而非整文件覆盖：
1. **`parse_retry_after_seconds`**（最易）：把 v0.20.1 `retry_utils.py:38-87` 的函数体加进 fork，让 `conversation_loop.py` 的内联 Retry-After 解析改调它。跑 `tests/test_retry_utils.py`。
2. **per-model reasoning_effort**（最有价值但最需小心）：
   - 对照 v0.20.1 `chat_completion_helpers.py:2284`「per-model override > global」的读取逻辑
   - 对照 v0.20.1 `config.py` 的 config schema（per-model 配置项键名）
   - 在 fork 上手动加同样的读取 + config 默认值
   - **必须 E2E**：起一个 temp HERMES_HOME，用不同模型验证 reasoning 实际生效

### 阶段 D：冲突热点专项保护
- **`plugins/platforms/feishu/adapter.py`**：合并后必须确认 `_patched_lark_execute` / SSL 重试仍在（v0.20.1 没这个补丁，容易在 diff 里被"对的版本"覆盖掉）。
- **`gateway/run.py`**（+8347）：差异最大。建议**不整体跟上游**，只挑你需要的 fix 用 `git log -p -S` 定位再手动移植。

### 阶段 E：验证
- `scripts/run_tests.sh`（fork 仓库的测试套件，全绿）
- temp HERMES_HOME 起真实 gateway，飞书端到端测：发消息、`/model-glm`、限流时 fallback 切换、SSL 重试日志仍在
- 确认 fork 专有功能全部健在

### 阶段 F：合并回 main
- resync 分支测试通过后，`git checkout main && git merge --no-ff resync/v0.20.1-pick`
- `git diff HEAD~1..HEAD` 抽查，确认飞书补丁等未被回滚
- 重启 gateway

---

## 4. 明确不做的事（风险红线）
- ❌ 不在 main 上直接 merge upstream（分叉历史 + 海量冲突 = 必炸）
- ❌ 不整文件覆盖 `gateway/run.py` / `conversation_loop.py` / `run_agent.py`（会丢 fork 自定义）
- ❌ 不覆盖 `feishu/adapter.py`（会丢 SSL 重试补丁，上游没有）
- ❌ 不自动跑（fetch 都超时，自动化必然失败或半途损坏）

---

## 5. 我的建议（优先级）
1. **先用现有 v0.18.0 + 三项修复跑起来观察**（压缩阈值0.1 / 多级fallback / Z.AI中国端点）。你的真实痛点（限流、慢）已基本解决。
2. 整合是**独立的大工程**，建议专门开一个时间窗，按本方案阶段 A→F 人工推进。
3. 如果只为拿 `per-model reasoning_effort`，可只做阶段 C 第 2 步，不动其它。
