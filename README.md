<p align="center">
  <img src="assets/logo.png" width="150" alt="麦进化 Logo">
</p>

<h1 align="center">麦进化（MaiEvolve）</h1>

<p align="center">
  MaiBot 第三方插件：守护角色表达边界，异步审计对话风格，并沉淀经过校验的会话默契。
</p>

## 功能

- 在 Maisaka Planner 请求前注入人设锚点、临时纠偏约束和当前用户的会话策略。
- 仅在夜间复盘窗口且会话静默后，异步调用 MaiBot 配置的轻量模型，检查 AI 腔、重复套话、说教感、人设漂移、迎合和连续反问。
- 对近期审计中反复出现的问题创建带时限和轮次上限的临时纠偏规则。
- 按会话和发言者隔离收尾、篇幅节奏和话题边界策略；群聊策略按“群 + 发言者”存储，只在明确回复该发言者时注入。
- 只有明确正向反馈可用于表达沉淀；未回复或暂时沉默不视为负面反馈，也不会据此推断对方反感。
- 仅在检测到明确正向反馈、审计评分达标且模型确认表达安全时，使用 MaiBot SDK 写入原生 `Expression`。
- 通过 WebUI 管理人设锚点、审计限额、静默时间和策略衰减，并查看规则、策略卡及审计评分。

## 安装

将 `aaorxilv_mai_evolve` 文件夹放入 MaiBot 的 `plugins/` 目录。插件仅使用 `maibot-plugin-sdk` 和 Python 标准库，不需要额外安装第三方依赖。重启 MaiBot 后，在侧栏打开 **麦进化**。

在 **配置** 页检查审计模型任务名。默认使用 `fast_model`；该任务须在 MaiBot 模型配置中可用。插件默认等待会话静默 30 分钟，并仅在本机时区的 23:00—06:00 窗口内复盘，每日最多调用 30 次模型。可在侧栏配置页更改夜间窗口。

## 安全与数据边界

- 插件数据保存在 `data/plugins/aaorxilv.mai_evolve/mai_evolve.sqlite3`；不修改 `MaiBot.db` 表结构，不访问 MaiWeave 或 A_Memorix 的数据。
- 为生成会话策略，插件会在独立数据库中暂存被审计的用户发言和麦麦回复。交互记录、策略和审计记录保留最多 90 天。
- 写入本地或发送给审计模型前，会对邮箱、手机号、长数字串及常见凭据格式进行基础脱敏。
- 群聊策略按会话及发言者分别存储；回复器仅在 `reply_message_id` 能映射到原用户消息时注入对应成员策略，不会把个人策略广播给群聊。
- 正面反馈必须命中明确的感谢、赞扬或有帮助表达；“嗯”“好”“哈哈”和单纯继续互动都不作为正反馈。
- 暂时没有回复不会形成负面反馈信号；反思提示明确禁止从沉默推断对方反感、冷场或回避偏好。
- 原生表达只从私聊学习，并且必须逐字取自获明确正反馈的麦麦回复，通过最低审计分及表达安全校验；保存为 `checked=false`。未启用宿主的 `expression_checked_only` 时可参与原生召回；启用时需先经宿主人工审核。
- 当前 MaiBot 的 `Expression.modified_by` 使用 `AI` / `USER` 枚举，不支持任务书建议的 `mai_evolve` 值。因此插件用合法的 `AI` 标记原生记录，并在本地审计中保留该表达由 MaiEvolve 提交的状态。
- 模型调用受每日预算、静默时间和超时设置约束。审计在后台进行，模型失败时不新增策略或表达。

## 兼容性

- MaiBot：`1.4.8` 至 `1.4.99`。
- `maibot-plugin-sdk`：`2.21.0` 至 `2.23.x`。
- Python：以 MaiBot Runner 的 Python 环境为准。

## 测试

在 MaiBot 插件运行环境中执行：

```powershell
python -m unittest discover -s plugins/aaorxilv_mai_evolve/tests -v
python -m compileall -q plugins/aaorxilv_mai_evolve
node -e "const fs=require('fs');const h=fs.readFileSync('plugins/aaorxilv_mai_evolve/webui/index.html','utf8');const s=h.indexOf('<script>')+8,e=h.indexOf('</script>',s);new Function(h.slice(s,e));console.log('WebUI 脚本语法正常')"
```

重启 MaiBot 后打开侧栏 **麦进化**，保存一组配置并观察运行概览。要验证后台反思，可临时将静默分钟数调低，并把复盘窗口设为当前本地时间，发送多轮对话后查看审计记录；确认模型任务可用以及每日调用计数递增。群聊请使用两位发言者分别测试，并分别以回复和非回复消息触发生成，确认策略卡只注入给目标发言者。表达沉淀仅在私聊中测试，且必须有明确正向反馈。

## 目录

```text
plugins/aaorxilv_mai_evolve/
├── _manifest.json
├── config.py
├── plugin.py
├── core/
│   ├── critic.py
│   ├── injector.py
│   ├── reflexion.py
│   └── store.py
├── webui/
│   └── index.html
└── tests/
    ├── test_critic_and_injector.py
    ├── test_night_window.py
    ├── test_relationship.py
    └── test_privacy.py
```

## 作者与许可

作者：**aaorxilv**

本项目采用 GPL-3.0-or-later，详见 [_manifest.json](_manifest.json)。
