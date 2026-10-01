"""Local-file CLI; no environment loading, credentials or network clients."""
import argparse
import json
import sys

from . import import_kaizen, load_map, make_plan, read_html, reread, save_map, suggest, verify
from .filler import local_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    read = commands.add_parser("read")
    read.add_argument("source")
    read.add_argument("--platform", required=True)
    read.add_argument("--form", required=True)
    read.add_argument("--output-dir", default="form_maps")
    read.add_argument("--previous", help="Existing map to reconcile before writing")
    read.add_argument("--kaizen-json", action="store_true")
    plan = commands.add_parser("plan")
    plan.add_argument("map")
    plan.add_argument("values")
    plan.add_argument("--allow-candidates", action="store_true")
    review = commands.add_parser("verify")
    review.add_argument("map")
    review.add_argument("--field", required=True)
    review.add_argument("--concept", help="Explicitly assign meaning before confirming")
    args = parser.parse_args(argv)
    try:
        if args.command == "read":
            source = local_path(args.source).read_text(encoding="utf-8")
            if args.kaizen_json and args.platform != "kaizen":
                raise ValueError("Kaizen JSON import requires --platform kaizen")
            form = import_kaizen(json.loads(source), args.form) if args.kaizen_json else read_html(source, args.platform, args.form)
            output = local_path(args.output_dir)
            existing = local_path(args.previous) if args.previous else output / form.platform / f"{form.form_id}.json"
            if existing.exists():
                form = reread(load_map(existing), form)
            save_map(form, output)
            print(form.to_json(), end="")
        elif args.command == "plan":
            form = load_map(local_path(args.map))
            values = json.loads(local_path(args.values).read_text(encoding="utf-8"))
            print(make_plan(form, values, allow_candidates=args.allow_candidates).to_json(), end="")
        else:
            path = local_path(args.map)
            form = load_map(path)
            if args.concept:
                form = suggest(form, args.field, args.concept)
            form = verify(form, args.field)
            path.write_text(form.to_json(), encoding="utf-8")
            print(form.to_json(), end="")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"form-engine: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
