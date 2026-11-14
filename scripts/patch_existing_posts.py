"""Copy the small-screen CSS block from s3/blog/template.html into blog posts
that were generated before it existed.

Every blog/{date}.html has the template's CSS baked in at generation time, so
a later template CSS change only reaches new posts. This inserts the
template's "KaiOS-sized screens" @media block right before each post's
</style> -- nothing else in the post (Gemini copy, words, chart script) is
touched, and nothing is regenerated. Safe to re-run: posts that already have
the block are skipped.

Local files only, no AWS calls. Typical use, from this directory:

    aws s3 sync s3://<bucket>/blog/ blog-posts/ --exclude "*" --include "20*.html"
    python3 patch_existing_posts.py ../s3/blog/template.html blog-posts/
    aws s3 cp blog-posts/ s3://<bucket>/blog/ --recursive --exclude "*" \\
        --include "20*.html" --content-type "text/html; charset=utf-8"
"""

import re
import sys
from pathlib import Path

BLOCK_START = "  /* KaiOS-sized screens."
ALREADY_PATCHED = "@media (max-width: 240px)"
DATED_POST = re.compile(r"^\d{4}-\d{2}-\d{2}\.html$")


def extract_block(template_text):
    start = template_text.find(BLOCK_START)
    end = template_text.find("</style>", start)
    if start == -1 or end == -1:
        sys.exit(f"Couldn't find the block starting with {BLOCK_START!r} in the template")
    return template_text[start:end]


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: python3 patch_existing_posts.py <template.html> <posts-dir>")
    block = extract_block(Path(sys.argv[1]).read_text(encoding="utf-8"))
    posts = sorted(p for p in Path(sys.argv[2]).iterdir() if DATED_POST.match(p.name))

    patched = skipped = 0
    for post in posts:
        text = post.read_text(encoding="utf-8")
        if ALREADY_PATCHED in text:
            skipped += 1
            continue
        if text.count("</style>") != 1:
            print(f"WARNING: {post.name} doesn't have exactly one </style>, left alone")
            continue
        post.write_text(text.replace("</style>", block + "</style>"), encoding="utf-8")
        patched += 1

    print(f"{patched} patched, {skipped} already had the block, {len(posts)} dated posts total")


if __name__ == "__main__":
    main()
