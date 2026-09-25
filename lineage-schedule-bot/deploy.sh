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
echo "▶ 디스코드 Developer Portal > General Information > Interactions Endpoint URL: $URL/discord/interactions"
echo "▶ 톡브릿지 자체 서비스 URL (보류):   $URL/kakao/webhook"
echo "▶ env.yaml의 BOT_BASE_URL 을 $URL 로 채우고 한 번 더 ./deploy.sh"
