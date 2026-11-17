#!/usr/bin/env bash
# Lets GitHub Actions deploy to Google Cloud without any stored keys
# (Workload Identity Federation), then saves the settings as GitHub repo variables.
#
# Run once, after deploy/setup.sh, from the project root:
#
#   PROJECT_ID=my-project bash deploy/github-setup.sh
#
# Needs: gcloud signed in as a project owner, and gh signed in with admin on the repo.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID, e.g. PROJECT_ID=my-project bash deploy/github-setup.sh}"
REGION="${REGION:-us-east1}"
REPO="${REPO:-MoonKang/eatclean}"
BRANCH="${BRANCH:-main}"
SITE_BUCKET="${SITE_BUCKET:-${PROJECT_ID}-inspection-map}"

POOL="github"
PROVIDER="github-actions"
DEPLOY_SA_NAME="github-deployer"
DEPLOY_SA="${DEPLOY_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
RUNTIME_SA="inspection-collector@${PROJECT_ID}.iam.gserviceaccount.com"

cd "$(dirname "$0")/.."
gcloud config set project "$PROJECT_ID"

# A new service account takes a short while before IAM recognizes it, so retry
# commands that grant access to or for it.
retry() {
  local n
  for n in 1 2 3 4 5 6; do
    if "$@"; then return 0; fi
    echo "  (attempt $n failed; retrying in $((n * 5))s)" >&2
    sleep $((n * 5))
  done
  "$@"
}

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

echo "== APIs"
gcloud services enable iamcredentials.googleapis.com sts.googleapis.com

echo "== Identity pool for GitHub"
gcloud iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools create "$POOL" --location=global --display-name="GitHub Actions"

# Only workflow runs from this repository's main branch are accepted.
CONDITION="assertion.repository == '${REPO}' && assertion.ref == 'refs/heads/${BRANCH}'"
if gcloud iam workload-identity-pools providers describe "$PROVIDER" \
     --location=global --workload-identity-pool="$POOL" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers update-oidc "$PROVIDER" \
    --location=global --workload-identity-pool="$POOL" --attribute-condition="$CONDITION"
else
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
    --location=global --workload-identity-pool="$POOL" \
    --display-name="GitHub ${REPO}" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="$CONDITION"
fi
WIF_PROVIDER="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"

echo "== Deployer service account"
gcloud iam service-accounts describe "$DEPLOY_SA" >/dev/null 2>&1 ||
  gcloud iam service-accounts create "$DEPLOY_SA_NAME" --display-name="GitHub Actions deployer"

# Allow GitHub runs from the repo (already narrowed to main by the condition) to act as the deployer.
retry gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}" \
  >/dev/null

# What a source deploy of the Cloud Run Job needs.
for role in roles/run.sourceDeveloper roles/serviceusage.serviceUsageConsumer; do
  retry gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$DEPLOY_SA" --role="$role" --condition=None >/dev/null
done
# The deployer may attach the collector's identity to the job, but cannot act as it otherwise.
retry gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member="serviceAccount:$DEPLOY_SA" --role=roles/iam.serviceAccountUser >/dev/null
# Publishing the web app's code files.
retry gcloud storage buckets add-iam-policy-binding "gs://$SITE_BUCKET" \
  --member="serviceAccount:$DEPLOY_SA" --role=roles/storage.objectAdmin >/dev/null

echo "== GitHub repository variables (not secrets: none of these grant access by themselves)"
gh variable set GCP_PROJECT_ID   --repo "$REPO" --body "$PROJECT_ID"
gh variable set GCP_REGION       --repo "$REPO" --body "$REGION"
gh variable set GCP_WIF_PROVIDER --repo "$REPO" --body "$WIF_PROVIDER"
gh variable set GCP_DEPLOY_SA    --repo "$REPO" --body "$DEPLOY_SA"
gh variable set SITE_BUCKET      --repo "$REPO" --body "$SITE_BUCKET"

cat <<EOF

Done. Pushes to ${BRANCH} now deploy automatically.
Try it without a code change:  gh workflow run deploy.yml --repo ${REPO}
EOF
