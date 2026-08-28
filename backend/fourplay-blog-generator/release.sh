#!/usr/bin/env bash
set -euo pipefail

IMAGE=fourplay-blog-generator
CONTAINER=${IMAGE}-instance
FUNCTION_NAME=fourplay-blog-generator

TIMESTAMP=$(date +%s)
ZIP=fourplay-blog-generator-lambda-release-${TIMESTAMP}.zip

# Clean old artifacts
rm -rf dimg

# Build image. --platform is pinned explicitly (not left to whatever the
# build machine defaults to) since this has to match the Lambda function's
# own configured architecture in AWS, or the deploy fails at invoke time
# with an exec-format error -- see DEPLOYMENT_STEPS.md's Lambda-creation
# step for the arm64 config this assumes.
docker build --platform linux/arm64 -t "${IMAGE}" .

# Ensure container is gone
docker rm -f "${CONTAINER}" 2>/dev/null || true

# Run container and extract deps
docker run -d --name "${CONTAINER}" "${IMAGE}"
docker cp "${CONTAINER}:/opt/python" dimg

# Add handler
cp lambda_function.py dimg/

# Zip lambda package
(
  cd dimg
  zip -vr "../${ZIP}" .
)

# Update Lambda
aws lambda update-function-code \
  --function-name "${FUNCTION_NAME}" \
  --zip-file "fileb://${ZIP}" \
  --no-cli-pager
