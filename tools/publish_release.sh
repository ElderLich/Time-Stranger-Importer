#!/usr/bin/env bash
# Invoked by the publish job after it downloads the verified installable ZIP.
set -euo pipefail

asset=dist/Time_Stranger_Importer.zip
notes=dist/release-notes.md
url="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY"
source_commit=$(git rev-parse HEAD)

if [[ "$AUTOMATIC_RELEASE" == true ]]; then
    # Keep master fast-forwardable for contributors. The release tag contains the
    # exact bl_info shipped in the ZIP, alongside the pushed source and docs.
    unzip -p "$asset" Time_Stranger_Importer/__init__.py > __init__.py
    git add -- __init__.py
    if git rev-parse -q --verify "refs/tags/$RELEASE_TAG" >/dev/null; then
        tagged=$(git rev-parse "$RELEASE_TAG^{commit}")
        if [[ "$tagged" != "$source_commit" && "$(git rev-parse "$tagged^")" != "$source_commit" ]]; then
            echo "$RELEASE_TAG already belongs to another push" >&2
            exit 1
        fi
        git diff --cached --exit-code "$tagged" --
    else
        if ! git diff --cached --quiet; then
            git -c user.name='github-actions[bot]' -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
                commit -m "Set Blender version to ${RELEASE_TAG#v} for distribution"
        fi
        git tag "$RELEASE_TAG"
        git push origin "refs/tags/$RELEASE_TAG"
    fi
fi

# Direct pushes get commit notes too, including pushes with no pull requests.
if [[ "$AUTOMATIC_RELEASE" == true && "$BEFORE" =~ ^[0-9a-f]{40}$ && ! "$BEFORE" =~ ^0+$ ]] && git cat-file -e "$BEFORE^{commit}"; then
    range="$BEFORE..$source_commit"
    changes_url="$url/compare/$BEFORE...$source_commit"
else
    previous=$(git describe --tags --abbrev=0 --match 'v[0-9]*' "$source_commit^" 2>/dev/null || true)
    range=${previous:+"$previous.."}$source_commit
    changes_url="$url/commits/$source_commit"
fi
{
    echo '## What changed'
    echo
    # An optional hand-written overview for this exact version precedes the commit list.
    awk -v title="## ${RELEASE_TAG#v}" '$0 == title { found=1; next } found && /^## / { exit } found { print }' docs/release-notes.md |
        sed -E "s@\]\(([[:alnum:]_.-]+\.md)(#[^)]*)?\)@]($url/blob/$RELEASE_TAG/docs/\1\2)@g"
    git log --reverse --format="- %s ([%h]($url/commit/%H))" "$range"
    echo
    echo "[Full changes]($changes_url) · [Install the Blender add-on]($url/blob/$RELEASE_TAG/README.md#install)"
} > "$notes"

# Resume an interrupted draft. A published release with its ZIP is already complete.
# Listing drafts via the authenticated repository endpoint also surfaces API failures.
gh api --paginate "repos/$GITHUB_REPOSITORY/releases?per_page=100" \
    --jq '.[] | [.tag_name, (if .draft then "draft" elif any(.assets[]; .name == "Time_Stranger_Importer.zip") then "published" else "missing-asset" end), .prerelease] | @tsv' > dist/releases.tsv
state=$(awk -F '\t' -v tag="$RELEASE_TAG" '$1 == tag {print $2}' dist/releases.tsv)
case "$state" in
    published) exit 0 ;;
    missing-asset) gh release upload "$RELEASE_TAG" "$asset"; exit 0 ;;
    draft) gh release upload "$RELEASE_TAG" "$asset" --clobber ;;
    "") gh release create "$RELEASE_TAG" "$asset" --draft --verify-tag --title "Time Stranger Importer $RELEASE_TAG" --notes-file "$notes" ;;
    *) echo "Unexpected release state: $state" >&2; exit 1 ;;
esac
# A late retry can finish after a newer push. Keep the highest stable version as Latest.
highest=$({ echo "$RELEASE_TAG"; awk -F '\t' '$2 != "draft" && $3 == "false" && $1 ~ /^v[0-9]+\.[0-9]+\.[0-9]+$/ {print $1}' dist/releases.tsv; } | sort -V | tail -1)
latest=false
if [[ "$highest" == "$RELEASE_TAG" ]]; then latest=true; fi
gh release edit "$RELEASE_TAG" --draft=false --latest="$latest" --title "Time Stranger Importer $RELEASE_TAG" --notes-file "$notes"
