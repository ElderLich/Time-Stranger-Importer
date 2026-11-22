"""Report captured actor motions, camera, VFX and sound, and optionally validate a staged battle pack."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.rearise_plan import inspect_actor, inspect_pack


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model', type=Path, help='Original Digimon_Library/<actor>/model/model.glb')
    parser.add_argument('--pack', type=Path, help='Staged Reloaded II mod root')
    parser.add_argument('--actor', help='Native model code if renamed during export')
    parser.add_argument('--bind', nargs=2, action='append', metavar=('TIMELINE_KEY', 'NATIVE_SKILL_ID'),
                        help='Map an exact captured timeline to its staged Special; repeat per skill')
    parser.add_argument('--resources', type=Path, action='append', default=[],
                        help='Additional native asset root, e.g. extracted app_0.dx11; repeat in lookup order')
    args = parser.parse_args()
    if (args.bind or args.actor or args.resources) and not args.pack:
        parser.error('--bind, --actor and --resources require --pack')
    try:
        plan = inspect_actor(args.model)
        if args.pack:
            bindings = [(key, int(value)) for key, value in args.bind or ()]
            plan['staged'] = inspect_pack(plan, args.pack, args.actor or plan['source_model'], bindings, args.resources)
    except (OSError, ValueError, KeyError, IndexError) as exc:
        parser.exit(2, f'{parser.prog}: {exc}\n')
    print(json.dumps(plan, indent=2))
    return int(bool(plan.get('staged', {}).get('errors')))


if __name__ == '__main__':
    raise SystemExit(main())
