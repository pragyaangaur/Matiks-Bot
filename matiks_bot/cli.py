"""Command line entry point: python -m matiks_bot.cli <command>"""

from __future__ import annotations

import argparse
import sys

from .config import load_config


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="read and solve questions but never type an answer")
    parser.add_argument("--quiet", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="matiks-bot", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    play = sub.add_parser("play", help="run the browser bot (recommended)")
    _add_common(play)
    play.add_argument("--games", type=int, help="stop after N games")
    play.add_argument("--minutes", type=float, help="stop after N minutes")
    play.add_argument("--response-min", type=float,
                      help="fastest time-to-answer, seconds (default 0.8)")
    play.add_argument("--response-max", type=float,
                      help="slowest time-to-answer, seconds (default 1.2)")
    play.add_argument("--no-nav", action="store_true",
                      help="assume a game is already open; skip menu navigation")
    play.add_argument("--handoff", action="store_true",
                      help="open the browser, wait for you to log in and start a duel, "
                           "then take over automatically (recommended)")
    play.add_argument("--handoff-minutes", type=float, default=15,
                      help="how long to wait for you during handoff")
    play.add_argument("--debug-detect", action="store_true",
                      help="while waiting, log the biggest text on screen")
    play.add_argument("--capture-dir",
                      help="save DOM + screenshots of the duel screen for analysis")

    login = sub.add_parser("login", help="open a browser window so you can sign in once")
    _add_common(login)
    login.add_argument("--wait-minutes", type=float, default=10,
                       help="how long to hold the window open waiting for you")

    probe = sub.add_parser("probe", help="print what the bot can see on the current page")
    _add_common(probe)
    probe.add_argument("--watch", action="store_true", help="keep printing until Ctrl-C")

    android = sub.add_parser("android", help="run the phone fallback (adb + OCR)")
    _add_common(android)
    android.add_argument("--serial", help="adb device serial, if more than one is attached")

    cal = sub.add_parser("calibrate", help="map the phone's question area and keypad")
    _add_common(cal)
    cal.add_argument("--serial")

    solve_cmd = sub.add_parser("solve", help="solve an expression from the terminal (no browser)")
    solve_cmd.add_argument("expression", nargs="+")

    return parser


def cmd_play(args, config) -> int:
    from .browser_bot import MatiksBrowserBot

    if args.games is not None:
        config["run"]["max_games"] = args.games
    if args.minutes is not None:
        config["run"]["max_minutes"] = args.minutes
    if args.response_min is not None:
        config["pacing"]["response_min_s"] = args.response_min
    if args.response_max is not None:
        config["pacing"]["response_max_s"] = args.response_max
    if config["pacing"]["response_min_s"] > config["pacing"]["response_max_s"]:
        print("error: --response-min must not exceed --response-max", file=sys.stderr)
        return 1

    bot = MatiksBrowserBot(config, dry_run=args.dry_run, verbose=not args.quiet,
                           debug_detect=args.debug_detect,
                           capture_dir=args.capture_dir)
    bot.start()
    try:
        if args.handoff:
            # Surface the "Matiks on Browser" modal, then get out of the way.
            # The terms checkbox and the login are yours to click, not mine.
            bot.page.wait_for_timeout(6000)
            bot.click_any(["Play On Desktop", "PLAY ON BROWSER"], timeout_ms=5000)
            print("\n" + "=" * 68)
            print("Browser is open and waiting. In that window:")
            print("  1. tick the terms checkbox and sign in")
            print("  2. go to Math -> Sprint Duels and start a duel")
            print("The bot starts answering as soon as it sees a question.")
            print("=" * 68 + "\n")
            if not bot.wait_for_game(timeout_s=args.handoff_minutes * 60):
                print("No duel appeared in time. Rerun when you're ready.", file=sys.stderr)
                return 1
            stats = bot.run()
            print(stats.summary())
            return 0

        bot.wait_for_login()
        if not args.no_nav and not bot.navigate_to_game():
            print("Could not reach a question screen automatically.\n"
                  "Start a game by hand in the open window, then rerun with --no-nav.",
                  file=sys.stderr)
            return 1
        stats = bot.run()
    except KeyboardInterrupt:
        stats = bot.stats
        print("\ninterrupted")
    finally:
        bot.stop()

    print(stats.summary())
    if stats.unparsed:
        print("\nQuestions it could not parse (paste these back to extend solver.py):")
        for question in stats.unparsed[:20]:
            print(f"  {question!r}")
    return 0


def cmd_login(args, config) -> int:
    """Hold a browser window open until a human has signed in.

    The session is stored in browser.profile_dir, so this is a one-time step —
    later `play` runs reuse it. Nothing here reads or stores your password; it
    just waits for the sign-in buttons to disappear.
    """
    from .browser_bot import MatiksBrowserBot

    bot = MatiksBrowserBot(config, dry_run=True, verbose=not args.quiet)
    bot.start()
    try:
        print("A Chromium window is open. Sign in to Matiks there — I can't and won't type\n"
              "your credentials. I'll detect it and save the session automatically.")
        bot.wait_for_login(timeout_s=args.wait_minutes * 60)
        print(f"Signed in. Session saved to {config['browser']['profile_dir']} — "
              "you won't need to do this again.")
        return 0
    except TimeoutError:
        print("Timed out waiting for sign-in. Rerun `login` when you're ready.", file=sys.stderr)
        return 1
    finally:
        bot.stop()


def cmd_probe(args, config) -> int:
    import json
    import time

    from .browser_bot import MatiksBrowserBot

    bot = MatiksBrowserBot(config, dry_run=True, verbose=not args.quiet)
    bot.start()
    try:
        while True:
            print(json.dumps(bot.read_state(), indent=2)[:4000])
            if not args.watch:
                break
            time.sleep(1.5)
            print("-" * 60)
    except KeyboardInterrupt:
        pass
    finally:
        bot.stop()
    return 0


def cmd_android(args, config) -> int:
    from .android_bot import MatiksAndroidBot

    bot = MatiksAndroidBot(config, serial=args.serial, dry_run=args.dry_run, verbose=not args.quiet)
    try:
        stats = bot.run()
    except KeyboardInterrupt:
        stats = bot.stats
        print("\ninterrupted")
    print(stats.summary())
    return 0


def cmd_calibrate(args, config) -> int:
    from .calibrate import calibrate

    calibrate(config, args.config, serial=args.serial)
    return 0


def cmd_solve(args) -> int:
    from .solver import SolveError, solve

    expression = " ".join(args.expression)
    try:
        print(solve(expression))
    except SolveError as exc:
        print(f"could not solve {expression!r}: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "solve":
        return cmd_solve(args)

    config = load_config(args.config)
    handlers = {
        "play": cmd_play,
        "login": cmd_login,
        "probe": cmd_probe,
        "android": cmd_android,
        "calibrate": cmd_calibrate,
    }
    try:
        return handlers[args.command](args, config)
    except (RuntimeError, TimeoutError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
