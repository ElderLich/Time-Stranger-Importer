"""Captured New Century skills to reviewable native candidates.

The public API does not import Blender. A candidate is explicitly incomplete
until its native asset bindings are completed and game appearance is accepted.
Battle execution belongs to the game; this package authors assets only.
"""
from .compiler import create_recipe, inspect_recipe, build_candidate, pin_file

__all__ = ['create_recipe', 'inspect_recipe', 'build_candidate', 'pin_file']
