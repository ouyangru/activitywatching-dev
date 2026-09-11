#!/usr/bin/env bash
# 一键发布：支持两种运行位置
#   1. 开发机：本地 main -> GitHub -> 阿里云服务器
#   2. 生产服务器 /opt/activity-timeline：GitHub main -> 当前服务器（跳过 push / SSH 自己）
#
# 用法（在仓库根目录，Git Bash / Linux 均可）：
#   bash scripts/deploy-aliyun.sh
#
# 开发机前置条件：
#   1. 本地已提交所有改动（工作树不干净会拒绝部署）
#   2. 本地 GitHub SSH key 可用
#   3. 可免密 SSH 登录生产服务器
#
# 生产服务器前置条件：
#   1. 当前仓库位于 ACTIVITY_DEPLOY_DIR（默认 /opt/activity-timeline）
#   2. 当前用户可 sudo 管理 activity-timeline 服务
#
# 服务器生产副本以 origin/main 为准；服务器上的未提交改动不会被保留。

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
command -v curl >/dev/null || die "缺少 curl"

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# 生产服务器上的仓库是部署副本，不应该再反向 push GitHub。
SELF_DEPLOY=0
if [ "$(pwd -P)" = "$(realpath -m "$APP_DIR")" ] && command -v systemctl >/dev/null 2>&1; then
  SELF_DEPLOY=1
fi

if [ "$SELF_DEPLOY" -eq 0 ]; then
  command -v ssh >/dev/null || die "缺少 ssh"
  [ -z "$(git status --porcelain)" ] || die "工作树有未提交改动，请先 commit（git status 查看）"
fi

say "同步 origin/$BRANCH"
git fetch origin "$BRANCH"

if [ "$SELF_DEPLOY" -eq 1 ]; then
  OLD_COMMIT="$(git rev-parse HEAD)"
  printf '%s' "$OLD_COMMIT" > /tmp/aw-deploy-old-commit
  say "检测到当前就在生产服务器，跳过 git push，直接更新到 origin/$BRANCH"
  git reset --hard "origin/$BRANCH"
else
  LOCAL_SHA="$(git rev-parse HEAD)"
  REMOTE_SHA="$(git rev-parse "origin/$BRANCH")"

  if [ "$LOCAL_SHA" = "$REMOTE_SHA" ]; then
    say "本地 $BRANCH 已与 GitHub 一致"
  elif git merge-base --is-ancestor "$LOCAL_SHA" "$REMOTE_SHA"; then
    # GitHub 比本地更新，且没有分叉时安全快进；避免 fetch-first 报错。
    say "GitHub $BRANCH 比本地更新，先 fast-forward 到最新提交"
    git merge --ff-only "origin/$BRANCH"
  elif git merge-base --is-ancestor "$REMOTE_SHA" "$LOCAL_SHA"; then
    say "推送 $BRANCH 到 origin（当前提交 $(git rev-parse --short HEAD)）"
    git push origin "$BRANCH"
  else
    die "本地 $BRANCH 与 origin/$BRANCH 已分叉。请先人工 rebase/merge，脚本不会自动覆盖任何一侧。"
  fi
fi

ensure_env_key_local() {
  local key="$1"
  local value="$2"
  if ! sudo grep -q "^${key}=" /etc/activity-timeline.env 2>/dev/null; then
    printf '%s=%s\n' "$key" "$value" | sudo tee -a /etc/activity-timeline.env >/dev/null
  fi
}

upgrade_current_server() {
  say "安装依赖、升级配置并重启当前服务器"
  sudo touch /etc/activity-timeline.env
  sudo chmod 600 /etc/activity-timeline.env

  "$APP_DIR/.venv/bin/pip" install -q -r backend/requirements.txt

  ensure_env_key_local ACTIVITYWATCH_DEBUG_VIEW 1
  ensure_env_key_local QQ_EMAIL ""
  ensure_env_key_local QQ_EMAIL_AUTH_CODE ""
  ensure_env_key_local QQ_IMAP_HOST imap.qq.com
  ensure_env_key_local QQ_IMAP_PORT 993
  ensure_env_key_local QQ_IMAP_MAILBOX INBOX
  ensure_env_key_local RECRUITMENT_AUTO_SCAN 1
  ensure_env_key_local RECRUITMENT_SCAN_INTERVAL_SECONDS 600
  ensure_env_key_local GOOGLE_CALENDAR_CLIENT_ID ""
  ensure_env_key_local GOOGLE_CALENDAR_CLIENT_SECRET ""
  ensure_env_key_local GOOGLE_CALENDAR_ID primary
  ensure_env_key_local GOOGLE_CALENDAR_REDIRECT_URI ""

  local service_file="/etc/systemd/system/$SERVICE.service"
  if sudo grep -q 'backend.app.main:app' "$service_file"; then
    sudo sed -i 's/backend\.app\.main:app/backend.app.recruitment_entry:app/g' "$service_file"
  fi
  sudo systemctl daemon-reload
  sudo systemctl restart "$SERVICE"
}

if [ "$SELF_DEPLOY" -eq 1 ]; then
  upgrade_current_server
else
  # 开发机模式：远端记录旧版本 -> 拉取 -> 依赖 -> 升级配置 -> 重启。
  say "服务器拉取最新代码并重启服务"
  ssh "$SERVER" "set -e
    cd $APP_DIR
    git rev-parse HEAD | tr -d '\n' > /tmp/aw-deploy-old-commit
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
    grep -q '^GOOGLE_CALENDAR_CLIENT_ID=' \"\$ENV_FILE\" || echo 'GOOGLE_CALENDAR_CLIENT_ID=' >> \"\$ENV_FILE\"
    grep -q '^GOOGLE_CALENDAR_CLIENT_SECRET=' \"\$ENV_FILE\" || echo 'GOOGLE_CALENDAR_CLIENT_SECRET=' >> \"\$ENV_FILE\"
    grep -q '^GOOGLE_CALENDAR_ID=' \"\$ENV_FILE\" || echo 'GOOGLE_CALENDAR_ID=primary' >> \"\$ENV_FILE\"
    grep -q '^GOOGLE_CALENDAR_REDIRECT_URI=' \"\$ENV_FILE\" || echo 'GOOGLE_CALENDAR_REDIRECT_URI=' >> \"\$ENV_FILE\"

    SERVICE_FILE=/etc/systemd/system/$SERVICE.service
    if grep -q 'backend.app.main:app' \"\$SERVICE_FILE\"; then
      sed -i 's/backend\.app\.main:app/backend.app.recruitment_entry:app/g' \"\$SERVICE_FILE\"
    fi
    systemctl daemon-reload
    systemctl restart $SERVICE"
fi

# 健康检查（带超时重试）
say "健康检查（最长等待 ${HEALTH_TIMEOUT}s）"
deadline=$((SECONDS + HEALTH_TIMEOUT))
until curl -fsS -m 5 "$BASE_URL$HEALTH_PATH" >/dev/null 2>&1; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    say "健康检查失败，自动回滚"
    if [ "$SELF_DEPLOY" -eq 1 ]; then
      OLD_COMMIT="$(cat /tmp/aw-deploy-old-commit)"
      git reset --hard "$OLD_COMMIT" || true
      sudo systemctl restart "$SERVICE" || true
      sudo journalctl -u "$SERVICE" -n 30 --no-pager || true
    else
      OLD_COMMIT="$(ssh "$SERVER" "cat /tmp/aw-deploy-old-commit")"
      ssh "$SERVER" "cd $APP_DIR && sudo -u $GIT_USER git reset --hard $OLD_COMMIT && systemctl restart $SERVICE" || true
      ssh "$SERVER" "journalctl -u $SERVICE -n 30 --no-pager" || true
    fi
    die "部署失败，已回滚到 $OLD_COMMIT，请根据上方日志排查"
  fi
  sleep 3
done

# 服务状态确认与配置提示。
if [ "$SELF_DEPLOY" -eq 1 ]; then
  SERVICE_STATE="$(sudo systemctl is-active "$SERVICE")"
  MAIL_STATE="$(sudo sh -c "if grep -Eq '^QQ_EMAIL=.+$' /etc/activity-timeline.env && grep -Eq '^QQ_EMAIL_AUTH_CODE=.+$' /etc/activity-timeline.env; then echo configured; else echo missing; fi")"
  GOOGLE_STATE="$(sudo sh -c "if grep -Eq '^GOOGLE_CALENDAR_CLIENT_ID=.+$' /etc/activity-timeline.env && grep -Eq '^GOOGLE_CALENDAR_CLIENT_SECRET=.+$' /etc/activity-timeline.env; then echo configured; else echo missing; fi")"
else
  SERVICE_STATE="$(ssh "$SERVER" "systemctl is-active $SERVICE")"
  MAIL_STATE="$(ssh "$SERVER" "if grep -Eq '^QQ_EMAIL=.+$' /etc/activity-timeline.env && grep -Eq '^QQ_EMAIL_AUTH_CODE=.+$' /etc/activity-timeline.env; then echo configured; else echo missing; fi")"
  GOOGLE_STATE="$(ssh "$SERVER" "if grep -Eq '^GOOGLE_CALENDAR_CLIENT_ID=.+$' /etc/activity-timeline.env && grep -Eq '^GOOGLE_CALENDAR_CLIENT_SECRET=.+$' /etc/activity-timeline.env; then echo configured; else echo missing; fi")"
fi
[ "$SERVICE_STATE" = "active" ] || die "服务状态异常: $SERVICE_STATE"

printf '\n✅ 部署完成：%s\n' "$(git log -1 --pretty=format:'%h %s')"
printf '   入口: %s （服务 %s，数据库与访问令牌未变动）\n' "$BASE_URL" "$SERVICE"
if [ "$MAIL_STATE" != "configured" ]; then
  printf '   ⚠ QQ 邮箱尚未配置完整：请在服务器 /etc/activity-timeline.env 填写 QQ_EMAIL 和 QQ_EMAIL_AUTH_CODE 后重启服务。\n'
fi
if [ "$GOOGLE_STATE" != "configured" ]; then
  printf '   ℹ Google Calendar 尚未配置：本地日历可正常使用；需要同步时再填写 GOOGLE_CALENDAR_CLIENT_ID / GOOGLE_CALENDAR_CLIENT_SECRET / GOOGLE_CALENDAR_REDIRECT_URI。\n'
fi
