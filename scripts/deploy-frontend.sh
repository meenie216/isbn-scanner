#!/usr/bin/env bash
# deploy-frontend.sh — uploads the frontend to S3 and invalidates CloudFront.
# Use this after frontend-only changes to avoid a full SAM build/deploy cycle.
set -euo pipefail

REGION="ap-southeast-2"
STACK="isbn-scanner"

echo ""
echo "═══════════════════════════════════════════"
echo "  ISBN Scanner — Frontend Deploy (${REGION})"
echo "═══════════════════════════════════════════"
echo ""

# ── 1. Validate required env vars ─────────────────────────────────────────────
: "${AWS_ACCESS_KEY_ID:?  Missing AWS_ACCESS_KEY_ID in .env}"
: "${AWS_SECRET_ACCESS_KEY:?  Missing AWS_SECRET_ACCESS_KEY in .env}"

echo "▶  AWS account: $(aws sts get-caller-identity --query Account --output text --region ${REGION})"
echo ""

# ── 2. Fetch stack outputs ────────────────────────────────────────────────────
echo "▶  Fetching stack outputs..."

_stack_output() {
  aws cloudformation describe-stacks \
    --stack-name "${STACK}" \
    --region "${REGION}" \
    --query "Stacks[0].Outputs[?OutputKey=='${1}'].OutputValue" \
    --output text
}

API_URL=$(_stack_output ApiUrl)
BUCKET=$(_stack_output FrontendBucket)

if [[ -z "${BUCKET}" || "${BUCKET}" == "None" ]]; then
  echo "ERROR: Could not determine S3 bucket from stack outputs." >&2
  echo "       Has the stack been deployed? Run deploy.sh first." >&2
  exit 1
fi

# ── 3. Inject API URL into config.js ─────────────────────────────────────────
echo "▶  Writing API URL to frontend/js/config.js..."
DEPLOY_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
printf 'window.API_BASE = "%s";\nwindow.DEPLOY_TIME = "%s";\n' "${API_URL}" "${DEPLOY_TIME}" > frontend/js/config.js
echo "   ✔  API URL set: ${API_URL}"

# ── 4. Upload frontend to S3 ──────────────────────────────────────────────────
echo ""
echo "▶  Uploading frontend to S3 (s3://${BUCKET}/)..."
aws s3 sync frontend/ "s3://${BUCKET}/" \
  --delete \
  --region "${REGION}"
echo "   ✔  Frontend uploaded"

# ── 5. Invalidate CloudFront cache ────────────────────────────────────────────
echo ""
echo "▶  Invalidating CloudFront cache..."
DISTRIBUTION_ID=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?Origins.Items[?contains(DomainName,'${BUCKET}')]].Id" \
  --output text)

if [[ -n "${DISTRIBUTION_ID}" && "${DISTRIBUTION_ID}" != "None" ]]; then
  aws cloudfront create-invalidation \
    --distribution-id "${DISTRIBUTION_ID}" \
    --paths "/*" > /dev/null
  echo "   ✔  Cache invalidated — changes live immediately"
else
  echo "   ⚠  Could not find CloudFront distribution — changes may take up to 24h to propagate"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
FRONTEND_URL=$(_stack_output FrontendUrl)
echo ""
echo "═══════════════════════════════════════════"
echo "  ✅  Frontend deploy complete!"
echo ""
echo "  🌐  App URL: https://${FRONTEND_URL#https://}"
echo "═══════════════════════════════════════════"
echo ""
