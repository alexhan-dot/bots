#!/bin/bash
# 사전: gcloud auth login / gcloud config set project <PROJECT_ID> / env.yaml 작성
set -e
SERVICE=lineage-schedule-bot
REGION=${REGION:-asia-northeast3}      # 서울. 시드니면 australia-southeast1
SA=${SA:-lineage-bot@$(gcloud config get-value project).iam.gserviceaccount.com}

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  firestore.googleapis.com speech.googleapis.com sheets.googleapis.com

# --min-instances 1 : 콜드 스타트 없음 (디스코드 3초 제한·즉시 응답용). 비용 줄이려면 MIN_INSTANCES=0
# --no-cpu-throttling : 응답을 보낸 뒤에도 CPU 유지 → 디스코드 저장·AI 파싱 같은 후속 작업이 끊기지 않음
gcloud run deploy $SERVICE --source . --region $REGION --allow-unauthenticated \
  --service-account $SA --env-vars-file env.yaml --memory 1Gi --timeout 120 \
  --min-instances ${MIN_INSTANCES:-1} --no-cpu-throttling

URL=$(gcloud run services describe $SERVICE --region $REGION --format 'value(status.url)')
TOKEN=$(grep '^TELEGRAM_BOT_TOKEN' env.yaml | cut -d'"' -f2)
SECRET=$(grep '^TELEGRAM_WEBHOOK_SECRET' env.yaml | cut -d'"' -f2 || true)
echo "서비스 URL: $URL"
curl -s "https://api.telegram.org/bot$TOKEN/setWebhook" \
  --data-urlencode "url=$URL/telegram/webhook" ${SECRET:+--data-urlencode "secret_token=$SECRET"} && echo
# 텔레그램 메뉴(/ 누르면 뜨는 명령 목록)
curl -s "https://api.telegram.org/bot$TOKEN/setMyCommands" -H "Content-Type: application/json" -d '{"commands":[
  {"command":"start","description":"사용법 · 내 Chat ID"},
  {"command":"week","description":"다음 주 스케줄 미리 만들기"},
  {"command":"glossary","description":"용어 사전 보기"}]}' >/dev/null && echo "텔레그램 메뉴 등록"

# 디스코드 Interactions Endpoint 자동 등록 (봇 토큰이 env.yaml 에 있을 때)
DTOKEN=$(grep '^DISCORD_BOT_TOKEN' env.yaml | cut -d'"' -f2 || true)
if [ -n "$DTOKEN" ]; then
  R=$(curl -s -o /dev/null -w '%{http_code}' -X PATCH "https://discord.com/api/v10/applications/@me" \
      -H "Authorization: Bot $DTOKEN" -H "Content-Type: application/json" \
      -d "{\"interactions_endpoint_url\":\"$URL/discord/interactions\"}")
  [ "$R" = "200" ] && echo "디스코드 Interactions Endpoint 등록: $URL/discord/interactions" \
                   || echo "⚠️ 디스코드 Endpoint 등록 실패($R) — Developer Portal 에서 직접: $URL/discord/interactions"
else
  echo "▶ 디스코드 Developer Portal > General Information > Interactions Endpoint URL: $URL/discord/interactions"
fi
grep -q "^BOT_BASE_URL: \"$URL\"" env.yaml || echo "▶ (선택) env.yaml BOT_BASE_URL 을 $URL 로 — 디스코드 카드 없이 웹훅만 쓸 때 링크용"
