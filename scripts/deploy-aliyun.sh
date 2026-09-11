#!/usr/bin/env bash
# 一键发布：本地 main -> GitHub -> 阿里云服务器（47.82.104.59）
#
# 用法（在仓库根目录，Git Bash / Linux 均可）：
#   bash scripts/deploy-aliyun.sh
#
# 前置条件：
#   1. 本地已提交所有改动（工作树不干净会拒绝部署）
#   2. 本地 GitHub SSH key 可用（origin 已配置为 git@github.com:...）
#   3. 可免密 SSH 登录 root@47.82.104.59
#
# 流程：干净树检查 -> push -> 服务器拉取+装依赖+升级配置+重启 -> 健康检查 -> 失败自动回滚
# 注意：服务器 /opt/activity-timeline 上的未提交改动会被 reset --hard 覆盖，
#       请始终在本地开发，服务器端临时改动先备份（参考 server-backup-20260906 分支）。

set -euo pipefail

SERVER="${ACTIVITY_DEPLOY_SERVER:-root@47.82.104.59}"
APP_DIR="${ACTIVITY_DEPLOY_DIR:-/opt/activity-timeline}"
SERVICE="activity-timeline"
BASE_URL="${ACTIVITY_DEPLOY_URL:-https://47.82.104.59}"
BRANCH="main"
HEALTH_PATH="/api/v1/health"
HEALTH_TIMEOUT=90
GIT_USER="admin"

say() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

command -v git  >/dev/null || die "缺少 git"
command -v ssh  >/dev/null || die "缺少 ssh"
command -v curl >/dev/null || die "缺少 curl"

cd "$(git rev-parse --show-toplevel)"

# 1. 工作树必须干净（防止部署半成品代码）
[ -z "$(git status --porcelain)" ] || die "工作树有未提交改动，请先 commit（git status 查看）"
LOCAL_COMMIT=$(git rev-parse --short HEAD)

# 2. 推送到 GitHub
say "推送 $BRANCH 到 origin（当前提交 $LOCAL_COMMIT）"
git push origin "$BRANCH"

# 3. 服务器：记录旧版本 -> 拉取 -> 依赖 -> 升级配置 -> 重启
say "服务器拉取最新代码并重启服务"
ssh "$SERVER" "set -e
  cd $APP_DIR
  git rev-parse --short HEAD | tr -d '\n' > /tmp/aw-deploy-old-commit
  sudo -u $GIT_USER git fetch origin
  sudo -u $GIT_USER git reset --hard origin/$BRANCH
  sudo -u $GIT_USER -H $APP_DIR/.venv/bin/pip install -q -r backend/requirements.txt

  ENV_FILE=/etc/activity-timeline.env
  touch \"\$ENV_FILE\"
  chmod 600 \"\$ENV_FILE\"
  grep -q '^ACTIVITYWATCH_DEBUG_VIEW=' \"\$ENV_FILE\" || echo 'ACTIVITYWATCH_DEBUG_VIEW=1' >> \"\$ENV_FILE\"
  grep -q '^QQ_EMAIL=' \"\$ENV_FILE\" || echo 'QQ_EMAIL=' >> \"\$ENV_FILE\"
  grep -q '^QQ_EMAIL_AUTH_CODE=' \"\$ENV_FILE\" || echo 'QQ_EMAIL_AUTH_CODE=' >> \"\$ENV_FILE\"
  grep -q '^QQ_IMAP_HOST=' \"\$ENV_FILE\" || echo 'QQ_IMAP_HOST=imap.qq.com' >> \"\$ENV_FILE\"
  grep -q '^QQ_IMAP_PORT=' \"\$ENV_FILE\" || echo 'QQ_IMAP_PORT=993' >> \"\$ENV_FILE\"
  grep -q '^QQ_IMAP_MAILBOX=' \"\$ENV_FILE\" || echo 'QQ_IMAP_MAILBOX=INBOX' >> \"\$ENV_FILE\"
  grep -q '^RECRUITMENT_AUTO_SCAN=' \"\$ENV_FILE\" || echo 'RECRUITMENT_AUTO_SCAN=1' >> \"\$ENV_FILE\"
  grep -q '^RECRUITMENT_SCAN_INTERVAL_SECONDS=' \"\$ENV_FILE\" || echo 'RECRUITMENT_SCAN_INTERVAL_SECONDS=600' >> \"\$ENV_FILE\"

  SERVICE_FILE=/etc/systemd/system/$SERVICE.service
  if grep -q 'backend.app.main:app' \"\$SERVICE_FILE\"; then
    sed -i 's/backend\.app\.main:app/backend.app.recruitment_entry:app/g' \"\$SERVICE_FILE\"
  fi
  systemctl daemon-reload
  systemctl restart $SERVICE"

# 4. 健康检查（带超时重试）
say "健康检查（最长等待 ${HEALTH_TIMEOUT}s）"
deadline=$((SECONDS + HEALTH_TIMEOUT))
until curl -fsS -m 5 "$BASE_URL$HEALTH_PATH" >/dev/null 2>&1; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    say "健康检查失败，自动回滚"
    OLD_COMMIT=$(ssh "$SERVER" "cat /tmp/aw-deploy-old-commit")
    ssh "$SERVER" "cd $APP_DIR && sudo -u $GIT_USER git reset --hard $OLD_COMMIT && systemctl restart $SERVICE" || true
    ssh "$SERVER" "journalctl -u $SERVICE -n 30 --no-pager" || true
    die "部署失败，已回滚到 $OLD_COMMIT，请根据上方日志排查"
  fi
  sleep 3
done

# 5. 服务状态确认
SERVICE_STATE=$(ssh "$SERVER" "systemctl is-active $SERVICE")
[ "$SERVICE_STATE" = "active" ] || die "服务状态异常: $SERVICE_STATE"

MAIL_STATE=$(ssh "$SERVER" "if grep -Eq '^QQ_EMAIL=.+$' /etc/activity-timeline.env && grep -Eq '^QQ_EMAIL_AUTH_CODE=.+$' /etc/activity-timeline.env; then echo configured; else echo missing; fi")

printf '\n✅ 部署完成：%s\n' "$(git log -1 --pretty=format:'%h %s')"
printf '   入口: %s （服务 %s，数据库与访问令牌未变动）\n' "$BASE_URL" "$SERVICE"
if [ "$MAIL_STATE" != "configured" ]; then
  printf '   ⚠ QQ 邮箱尚未配置完整：请在服务器 /etc/activity-timeline.env 填写 QQ_EMAIL 和 QQ_EMAIL_AUTH_CODE 后重启服务。\n'
fi
