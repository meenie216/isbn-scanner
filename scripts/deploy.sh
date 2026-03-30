#!/usr/bin/env bash
# deploy.sh — full deployment script run inside the Docker container.
# Runs: SSM setup → SAM build → SAM deploy → frontend upload
set -euo pipefail

REGION="ap-southeast-2"
STACK="isbn-scanner"

echo ""
echo "═══════════════════════════════════════════"
echo "  ISBN Scanner — Deploy to AWS (${REGION})"
echo "═══════════════════════════════════════════"
echo ""

# ── 1. Validate required env vars ─────────────────────────────────────────────
: "${AWS_ACCESS_KEY_ID:?  Missing AWS_ACCESS_KEY_ID in .env}"
: "${AWS_SECRET_ACCESS_KEY:?  Missing AWS_SECRET_ACCESS_KEY in .env}"
: "${NEON_DB_URL:?  Missing NEON_DB_URL in .env}"
: "${OMDB_API_KEY:?  Missing OMDB_API_KEY in .env}"

GOOGLE_KEY="${GOOGLE_BOOKS_API_KEY:-none}"

echo "▶  AWS account: $(aws sts get-caller-identity --query Account --output text --region ${REGION})"
echo ""

# ── 2. Store secrets in SSM Parameter Store ───────────────────────────────────
echo "▶  Storing secrets in SSM Parameter Store..."

aws ssm put-parameter \
  --name "/isbn-scanner/db-url" \
  --value "${NEON_DB_URL}" \
  --type SecureString \
  --overwrite \
  --region "${REGION}" > /dev/null

aws ssm put-parameter \
  --name "/isbn-scanner/omdb-api-key" \
  --value "${OMDB_API_KEY}" \
  --type String \
  --overwrite \
  --region "${REGION}" > /dev/null

aws ssm put-parameter \
  --name "/isbn-scanner/google-books-api-key" \
  --value "${GOOGLE_KEY}" \
  --type String \
  --overwrite \
  --region "${REGION}" > /dev/null

echo "   ✔  SSM parameters stored"

# ── 3. Pre-install Lambda layer dependencies ──────────────────────────────────
echo ""
echo "▶  Installing Lambda layer dependencies..."
pip install --quiet \
  -r backend/layer/requirements.txt \
  -t backend/layer/python/ \
  --upgrade
echo "   ✔  Layer dependencies installed"

# ── 4. SAM build ──────────────────────────────────────────────────────────────
echo ""
echo "▶  Building SAM application..."
sam build --region "${REGION}"
echo "   ✔  Build complete"

# ── 5. SAM deploy ─────────────────────────────────────────────────────────────
echo ""
echo "▶  Deploying to AWS CloudFormation (this takes ~3 minutes on first run)..."
sam deploy \
  --stack-name "${STACK}" \
  --region "${REGION}" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides Stage=prod
echo "   ✔  Stack deployed"

# ── 6. Retrieve outputs ───────────────────────────────────────────────────────
echo ""
echo "▶  Fetching stack outputs..."

_stack_output() {
  aws cloudformation describe-stacks \
    --stack-name "${STACK}" \
    --region "${REGION}" \
    --query "Stacks[0].Outputs[?OutputKey=='${1}'].OutputValue" \
    --output text
}

API_URL=$(_stack_output ApiUrl)
FRONTEND_URL=$(_stack_output FrontendUrl)
BUCKET=$(_stack_output FrontendBucket)

if [[ -z "${BUCKET}" || "${BUCKET}" == "None" ]]; then
  echo "ERROR: Could not determine S3 bucket name from stack outputs." >&2
  echo "       Run: aws cloudformation describe-stacks --stack-name isbn-scanner --region ap-southeast-2" >&2
  exit 1
fi

# ── 7. Inject API URL into config.js ─────────────────────────────────────────
echo "▶  Writing API URL to frontend/js/config.js..."
printf 'window.API_BASE = "%s";\n' "${API_URL}" > frontend/js/config.js
echo "   ✔  API URL set: ${API_URL}"

# ── 8. Upload frontend to S3 ──────────────────────────────────────────────────
echo ""
echo "▶  Uploading frontend to S3..."
aws s3 sync frontend/ "s3://${BUCKET}/" \
  --delete \
  --region "${REGION}"
echo "   ✔  Frontend uploaded"

# ── 9. Invalidate CloudFront cache ────────────────────────────────────────────
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
  echo "   ⚠  Could not find distribution ID — new files may take up to 24h to propagate"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════"
echo "  ✅  Deployment complete!"
echo ""
echo "  🌐  App URL: https://${FRONTEND_URL#https://}"
echo "═══════════════════════════════════════════"
echo ""
