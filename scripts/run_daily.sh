#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/Users/hktmacmini/Misato/Ehime_Akiya_Patrol"
PYTHON="${BASE_DIR}/.venv/bin/python"
LOG_FILE="${BASE_DIR}/logs/patrol.log"

mkdir -p "${BASE_DIR}/logs"
cd "${BASE_DIR}"

{
  echo "============================================================"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 愛媛の空き家パトロール 日次実行開始"

  PATROL_OUT="$("${PYTHON}" "${BASE_DIR}/scripts/patrol.py" 2>&1)"
  echo "${PATROL_OUT}"

  # 差分有無に関わらず、最終更新日時と最新件数をWeb用JSONへ反映
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Web表示用JSONを生成中 (build_web_data.py)..."
  "${PYTHON}" "${BASE_DIR}/scripts/build_web_data.py"

  # Gitリポジトリ & originリモートが設定されている場合のみ自動push
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1 && git remote get-url origin >/dev/null 2>&1; then
    if [[ -n "$(git status --porcelain web/data/site_data.json data/ehime_akiya_db.csv config/ehime_akiya_sources.json)" ]]; then
      git add web/data/site_data.json data/ehime_akiya_db.csv config/ehime_akiya_sources.json
      git commit -m "chore: update akiya data"
      git push origin main
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] GitHub/Cloudflare Pages向けデータpush完了"
    else
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] web/data/site_data.json および data/ehime_akiya_db.csv に変更なし。git pushをスキップ"
    fi
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Gitリポジトリまたはorigin未設定のため git push をスキップ"
  fi

  DRAFT_JSON="$(echo "${PATROL_OUT}" | awk -F= '/^DRAFT_JSON=/{print $2}' | tail -n 1)"
  if [[ -n "${DRAFT_JSON}" && -f "${DRAFT_JSON}" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ドラフト検知 -> note下書き保存を実行: ${DRAFT_JSON}"
    if "${PYTHON}" "${BASE_DIR}/scripts/note_drafter.py" --draft-json "${DRAFT_JSON}"; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] note下書き保存完了 -> メール通知処理を実行"
      "${PYTHON}" "${BASE_DIR}/scripts/notify.py"
    else
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: note下書き保存が失敗したためメール通知をスキップしました"
    fi
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 新着・価格改定なし。note下書き保存・メール通知をスキップします。"
  fi

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 日次実行終了"
} >> "${LOG_FILE}" 2>&1
