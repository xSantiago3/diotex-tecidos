#!/usr/bin/env bash
set -euo pipefail

REPO_NAME="${REPO_NAME:-diotex-tecidos}"
GITHUB_USERNAME="${GITHUB_USERNAME:?Set GITHUB_USERNAME}"
GITHUB_TOKEN="${GITHUB_TOKEN:?Set GITHUB_TOKEN}"
DESCRIPTION="${DESCRIPTION:-Diotex Tecidos multi-agent backend on Google Cloud}"

API_RESPONSE=$(curl -sS -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${GITHUB_TOKEN}" \
  https://api.github.com/user/repos \
  -d "{\"name\":\"${REPO_NAME}\",\"private\":true,\"description\":\"${DESCRIPTION}\"}")

if echo "${API_RESPONSE}" | grep -q '"full_name"'; then
  echo "GitHub repo created: ${GITHUB_USERNAME}/${REPO_NAME}"
else
  echo "GitHub API response: ${API_RESPONSE}"
fi

if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "https://github.com/${GITHUB_USERNAME}/${REPO_NAME}.git"
else
  git remote add origin "https://github.com/${GITHUB_USERNAME}/${REPO_NAME}.git"
fi

git push -u "https://${GITHUB_USERNAME}:${GITHUB_TOKEN}@github.com/${GITHUB_USERNAME}/${REPO_NAME}.git" main
