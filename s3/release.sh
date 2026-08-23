BUCKET=daniel-townsend-fourplay

rm -rf css js
cp -r ../frontend/css .
cp -r ../frontend/js .
cp ../frontend/index.html .
cp ../frontend/admin.html .

# puzzles/* is excluded from the sync entirely — those files are written
# directly to the bucket by the Lambda's generate_puzzle() (see
# backend/lambda/fourplay/puzzle.py), and the local puzzles/ directory here
# only ever holds a sample file for local testing. A plain --delete sync
# without this exclude would wipe out real puzzle data on every deploy.
aws s3 sync . s3://$BUCKET --exclude "*.sh" --exclude "*.DS_Store" --exclude "puzzles/*" --delete
