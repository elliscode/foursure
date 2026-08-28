BUCKET=daniel-townsend-fourplay

rm -rf css js assets
cp -r ../frontend/css .
cp -r ../frontend/js .
cp -r ../frontend/assets .
cp ../frontend/index.html .
cp ../frontend/admin.html .

# img/ isn't wiped-and-replaced like css/js/assets above -- unlike those,
# it holds a file that only ever lives here (og-banner.png, the Open Graph
# banner -- checked in directly under s3/img/ since it has no reason to
# ship inside the KaiOS app bundle in frontend/). A plain rm -rf would
# delete it locally with nothing to restore it from. Just refresh whatever
# frontend/img/ actually has instead.
cp ../frontend/img/*.png img/

# puzzles/* is excluded from the sync entirely — those files are written
# directly to the bucket by the Lambda's generate_puzzle() (see
# backend/lambda/fourplay/puzzle.py), and the local puzzles/ directory here
# only ever holds a sample file for local testing. A plain --delete sync
# without this exclude would wipe out real puzzle data on every deploy.
#
# blog/* is excluded for the same reason — those dated posts are written
# directly to the bucket by fourplay-blog-generator (see
# backend/fourplay-blog-generator/lambda_function.py), and the local blog/
# directory here only holds template.html. Push template edits with
# `aws s3 cp s3/blog/template.html s3://$BUCKET/blog/template.html` instead
# of relying on this sync.
#
# sitemap.xml is excluded too, same reason — fourplay-blog-generator writes
# it directly to the bucket root on every run, and there's no local
# s3/sitemap.xml for this sync to match against, so without the exclude a
# plain --delete sync would wipe it out on the next frontend deploy.
aws s3 sync . s3://$BUCKET --exclude "*.sh" --exclude "*.DS_Store" --exclude "puzzles/*" --exclude "blog/*" --exclude "sitemap.xml" --delete
