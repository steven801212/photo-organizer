from __future__ import annotations

import argparse
from pathlib import Path

from .config import Config, default_config_path, load_config, save_config
from .engine import Organizer


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="photo-organizer")
    root.add_argument("--config", type=Path, default=default_config_path())
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--inbox", type=Path, required=True)
    init.add_argument("--library", type=Path, required=True)
    init.add_argument("--index-dir", type=Path)
    imp = commands.add_parser("import")
    imp.add_argument("--dry-run", action="store_true")
    commands.add_parser("reindex")
    commands.add_parser("undo")
    commands.add_parser("recover")
    commands.add_parser("organize-unsorted")
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "init":
        path = save_config(Config(args.inbox, args.library, index_dir=args.index_dir), args.config)
        print(f"設定已儲存：{path}")
        return
    organizer = Organizer(load_config(args.config))
    try:
        if args.command == "import":
            items = organizer.scan(print)
            for item in items:
                print(f"{item.action:10} {Path(item.source).name} -> {item.destination}")
            if not args.dry_run:
                print(f"Manifest：{organizer.execute(items, print)}")
        elif args.command == "reindex":
            print("新增 / 略過：", organizer.reindex(print))
        elif args.command == "undo":
            print("復原 / 失敗：", organizer.undo())
        elif args.command == "recover":
            print("清除未完成暫存檔：", organizer.recover())
        elif args.command == "organize-unsorted":
            print("移動 / 仍未分類：", organizer.reorganize_unsorted(print))
    finally:
        organizer.close()


if __name__ == "__main__":
    main()
