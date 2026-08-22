#!/bin/bash
cd lambda/
TIMESTAMP=$(date +%s)
zip -vr ../lambda-release-${TIMESTAMP}.zip . -x "*.DS_Store"
cd ../
aws lambda update-function-code --function-name=fourplay-api --zip-file=fileb://lambda-release-${TIMESTAMP}.zip --no-cli-pager
