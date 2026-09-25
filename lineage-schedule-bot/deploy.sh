#!/bin/bash
# 사전: gcloud auth login / gcloud config set project <PROJECT_ID> / env.yaml 작성
set -e
SERVICE=lineage-schedule-bot
REGION=${REGION:-asia-northeast3}      # 서울. 시드니면 australia-southeast1
SA=${SA:-lineage-bot@$(gcloud config get-value project).iam.gserviceaccount.com}

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  firestore.googleapis.com speech.googleapis.com sheets.googleapis.com

gcloud run deploy $SERVICE --source . --region $REGION --allow-unauthenticated \
  --service-account $SA --env-vars-file env.yaml --memory 1Gi --timeout 120

URL=$(gcloud run services describe $SERVICE --region $REGION --format 'value(status.url)')
TOKEN=$(grep '^TELEGRAM_BOT_TOKEN' env.yaml | cut -d'"' -f2)
SECRET=$(grep '^TELEGRAM_WEBHOOK_SECRET' env.yaml | cut -d'"' -f2 || true)
echo "서비스 URL: $URL"
curl -s "https://api.telegram.org/bot$TOKEN/setWebhook" \
  --data-urlencode "url=$URL/telegram/webhook" ${SECRET:+--data-urlencode "secret_token=$SECRET"} && echo
echo "▶ 톡브릿지 자체 서비스 URL:   $URL/kakao/webhook"
echo "▶ env.yaml의 BOT_BASE_URL 을 $URL 로 채우고 한 번 더 ./deploy.sh"
