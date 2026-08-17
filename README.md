# Simba EDM Marketing Skill

将 Simba 后台 EDM 邮件批量发送流程整理成可复用 Skill。

## 包含内容
- `SKILL.md`：Skill 说明与操作规范
- `scripts/send_edm_campaign.py`：通用发送脚本（支持 `unsent` / `not_sent_today`）

## 快速开始
```bash
python3 scripts/send_edm_campaign.py \
  --token "<TOKEN>" \
  --country IN \
  --target 5000 \
  --segment unsent
```

## 作为 Codex Skill 安装（本机）
将本目录复制或软链到：
`~/.codex/skills/simba-edm-marketing-sender`

例如：
```bash
ln -s /absolute/path/to/simba-edm-marketing-skill ~/.codex/skills/simba-edm-marketing-sender
```

> 注意：每次运行前都应向用户索取本次 token，不要写入仓库。
