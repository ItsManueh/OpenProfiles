"""
Command line to manage and open profiles without the graphical interface.

    python cli.py list
    python cli.py create NAME [--mode desktop|iphone] [--device "iPhone 15 Pro"]
                              [--engine webkit|chromium] [--quality smooth|sharp] [--theme dark|light]
    python cli.py open NAME [NAME ...] | --all
    python cli.py delete NAME
    python cli.py devices
    python cli.py install
"""

from __future__ import annotations

import argparse
import sys

import launcher
import logs
from profiles import (
    ENGINES,
    MODES,
    QUALITIES,
    THEMES,
    Profile,
    ProfileError,
    create_profile,
    delete_profile,
    load_profiles,
)


def print_profiles() -> None:
    profiles = load_profiles()
    if not profiles:
        print("There are no profiles. Create one with 'create'.")
        return
    print(f"\n{'NAME':<18}{'MODE':<10}{'DEVICE':<20}{'ENGINE':<10}USER-AGENT")
    print("-" * 120)
    for p in profiles.values():
        device = p.device if p.mode == "iphone" else "-"
        print(f"{p.name:<18}{p.mode:<10}{device:<20}{p.engine:<10}{p.user_agent}")
    print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Isolated browser profiles.")
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("list", help="List the profiles")

    create = commands.add_parser("create", help="Create a profile")
    create.add_argument("name")
    create.add_argument(
        "--mode",
        choices=MODES,
        default="desktop",
        help="desktop (full browser, always Chromium; default) or iphone (emulation)",
    )
    create.add_argument("--device", default="", help='E.g. "iPhone 15 Pro" (random by default)')
    create.add_argument("--engine", choices=ENGINES, default="webkit", help="iphone mode only")
    create.add_argument(
        "--quality",
        choices=QUALITIES,
        default="smooth",
        help="iPhone + WebKit only: smooth (fast) or sharp (x3 resolution)",
    )
    create.add_argument("--theme", choices=THEMES, default="dark", help="Browser color scheme")
    create.add_argument("--user-agent", default="", help="Custom user-agent (random by default)")

    open_ = commands.add_parser("open", help="Open one or more profiles at once")
    open_.add_argument("names", nargs="*")
    open_.add_argument("--all", action="store_true", help="Open every profile")

    delete = commands.add_parser("delete", help="Delete a profile and its data")
    delete.add_argument("name")

    commands.add_parser("devices", help="Show the available iPhones")
    commands.add_parser("install", help="Download the browsers")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logs.setup_logging()  # the command line also writes to data/logs/app.log
    try:
        if args.command == "list":
            print_profiles()
        elif args.command == "create":
            profile = create_profile(
                Profile(
                    name=args.name,
                    device=args.device,
                    engine=args.engine,
                    mode=args.mode,
                    quality=args.quality,
                    theme=args.theme,
                    user_agent=args.user_agent,
                ),
                launcher.iphone_devices(),
            )
            print(f"Profile '{profile.name}' created ({profile.mode}, {profile.engine}).")
            print(f"User-agent: {profile.user_agent or '(device default)'}")
        elif args.command == "open":
            profiles = load_profiles()
            names = list(profiles) if args.all else list(dict.fromkeys(args.names))
            if not names:
                parser.error("give profile names or use --all")
            missing = [n for n in names if n not in profiles]
            if missing:
                raise ProfileError(f"These profiles do not exist: {', '.join(missing)}")
            launcher.open_profiles([profiles[n] for n in names])
        elif args.command == "delete":
            delete_profile(args.name)
            print("Profile deleted.")
        elif args.command == "devices":
            for name, device in launcher.iphone_devices().items():
                print(f"  {name:<20} {device['user_agent']}")
        elif args.command == "install":
            launcher.install_browsers()
        else:
            parser.print_help()
    except (ProfileError, RuntimeError) as e:
        print(f"[!] {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
