"""Publish source-preserving editor documents with repeat-save protection."""

import json
import os
from pathlib import Path


def publish_document(document, owner, filepath, *, source_path, source_hash, overwrite=False):
    # Do not resolve away a symlink before the shared writer can reject it.
    destination = Path(os.path.abspath(filepath))
    if destination.is_symlink():
        raise ValueError('Refusing to replace a symbolic-link destination')
    identity = os.path.normcase(str(destination.resolve()))
    source_identity = os.path.normcase(str(Path(source_path).resolve()))
    published = json.loads(owner.get('DSTS_published_outputs', '{}'))
    expected = published.get(identity, source_hash if identity == source_identity else None)
    result = document.write(destination, overwrite=overwrite, expected_sha256=expected)
    published[identity] = result
    owner['DSTS_published_outputs'] = json.dumps(published)
    return result
