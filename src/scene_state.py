"""Keep importer cleanup limited to the data created by that import."""

import bpy

KINDS = ("objects", "collections", "meshes", "armatures", "actions", "materials",
         "node_groups", "images", "cameras", "lights", "sounds")


def snapshot():
    return {name: set(getattr(bpy.data, name)) for name in KINDS}


def rollback(before):
    for name in KINDS:
        blocks = getattr(bpy.data, name)
        for block in list(blocks):
            if block not in before[name]:
                blocks.remove(block, do_unlink=True)


def purge_new_orphans(before, skip_actions=True):
    for _ in range(4):
        removed = 0
        for name in KINDS:
            if name in {"objects", "collections"} or (skip_actions and name == "actions"):
                continue
            blocks = getattr(bpy.data, name)
            for block in list(blocks):
                if block not in before[name] and block.users == 0 and not block.use_fake_user:
                    blocks.remove(block)
                    removed += 1
        if not removed:
            break
