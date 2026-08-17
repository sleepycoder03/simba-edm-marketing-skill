---
name: simba-edm-marketing-sender
description: |
  在 Simba 后台按国家/客户分组批量发送 EDM 营销邮件，支持「未发送过(unsent)」和「今天未发送(not_sent_today)」两种规则。
  自动处理限流重试并生成 JSON 报告。每次执行必须索取当次 token。
---

# Simba EDM 营销邮件批量发送

## 何时使用
- 用户要在 Simba 后台批量发送邮件营销。
- 用户给出国家（如 `MY`、`IN`）和目标发送数量（如 1000/5000）。
- 用户要求按客户状态过滤：
  1) `unsent`：从未发送过
  2) `not_sent_today`：今天尚未发送

## 强制安全规则
1. **每次执行前必须索取当次 token**（Bearer 后面的字符串）。
2. **不要复用历史 token**，除非用户在当前轮再次明确提供。
3. **不要把 token 写入脚本、文件、日志或最终回复**。

## 默认执行步骤
1. 与用户确认参数：`country`、`target`、`segment`。
2. 向用户索取当前 token。
3. 运行脚本：`scripts/send_edm_campaign.py`。
4. 返回：`success/attempted/after totals/report path`。

## 命令模板
> 使用系统 Python（或你环境里的 Python）

### A) 给“从未发送过”客户发邮件
```bash
python3 scripts/send_edm_campaign.py \
  --token "<TOKEN>" \
  --country IN \
  --target 5000 \
  --segment unsent
```

### B) 给“今天未发送”客户发邮件
```bash
python3 scripts/send_edm_campaign.py \
  --token "<TOKEN>" \
  --country MY \
  --target 2328 \
  --segment not_sent_today \
  --timezone Asia/Shanghai
```

### C) 指定模板编码
```bash
python3 scripts/send_edm_campaign.py \
  --token "<TOKEN>" \
  --country IN \
  --target 1000 \
  --segment unsent \
  --template-code TL260813103
```

## 关键参数
- `--country`: 国家代码（如 `MY`, `IN`）
- `--target`: 目标发送数量
- `--segment`: `unsent` / `not_sent_today`
- `--template-code`: 可选，不传时自动取模板列表第一条
- `--max-batch`: 每批发送人数（默认 50）
- `--timezone`: 仅 `not_sent_today` 使用
- `--output`: 自定义输出报告路径

## 输出
- JSON 报告（默认输出到 `./outputs/`）：
  - before/after 总量
  - success/attempted/batchCount
  - 模板信息与批次明细
