TIMESTAMP=$(date +%s)
zip -r fourplay-${TIMESTAMP}.zip . \
  -x "*.zip" \
  -x "*.DS_Store" \
  -x "*.md" \
  -x "*release.sh" \
  -x "admin.html"
