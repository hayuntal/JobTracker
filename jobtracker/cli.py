"""Command line entry point.

    python -m jobtracker setup-telegram   # one-time: find and save your chat id
    python -m jobtracker run              # check + message anything new
    python -m jobtracker run --dry-run    # check + print, change nothing
    python -m jobtracker sources          # health check every source
    python -m jobtracker test-notify      # prove delivery works
    python -m jobtracker list             # show what's in the seen-state
    python -m jobtracker seed             # mark today's matches seen, silently
"""
import argparse
import logging
import sys
from typing import List

from .config import Settings, load_dotenv, load_search, write_env
from .matching import Matcher
from .notify import TelegramNotifier, build_summary
from .pipeline import STATE_FILE, pick_notifier, run, source_health
from .store import SeenStore

# Sources polled unless you override with --only / --enable.
DEFAULT_SOURCES = ["careers", "linkedin"]
OPT_IN_SOURCES = ["glassdoor", "facebook"]
ALL_SOURCES = DEFAULT_SOURCES + OPT_IN_SOURCES


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _resolve_sources(args) -> List[str]:
    if args.only:
        chosen = [s.strip() for s in args.only.split(",") if s.strip()]
    else:
        chosen = list(DEFAULT_SOURCES)
        if args.enable:
            chosen += [s.strip() for s in args.enable.split(",") if s.strip()]
    unknown = [s for s in chosen if s not in ALL_SOURCES]
    if unknown:
        sys.exit("Unknown source(s): %s\nAvailable: %s"
                 % (", ".join(unknown), ", ".join(ALL_SOURCES)))
    return chosen


def cmd_run(args) -> int:
    sources = _resolve_sources(args)
    print("Sources: %s\n" % ", ".join(sources))
    report = run(sources, dry_run=args.dry_run)

    print("\n" + "-" * 62)
    print("collected %d · matched %d · new %d" %
          (report.collected, report.matched, report.new))
    print(build_summary(report.new_jobs))

    if report.deferred:
        print("%d held back by DIGEST_MAX_JOBS — they arrive in the next run."
              % report.deferred)

    if report.failed_sources:
        print("\nSources with problems:")
        for r in report.failed_sources:
            print("  %-12s %s" % (r.source, r.error))

    if report.notify_error:
        print("\nDELIVERY FAILED: %s" % report.notify_error)
        print("Jobs were NOT marked as seen — the next run will retry them.")
        return 1
    if report.new and report.notified and not args.dry_run:
        print("\nDigest sent (%d job(s))." % report.sent)
    return 0


def cmd_sources(args) -> int:
    sources = _resolve_sources(args)
    health = source_health(sources)

    print("\n%-12s %-8s %6s %8s %8s %7s  %s"
          % ("SOURCE", "STATUS", "JOBS", "ISRAELI", "MATCHED", "TIME", "NOTE"))
    print("-" * 88)
    for s in health["sources"]:
        note = (s["error"] or "")[:44]
        print("%-12s %-8s %6d %8d %8d %6.1fs  %s"
              % (s["name"], "ok" if s["ok"] else "FAILED", s["jobs"],
                 s["israeli"], s["matched"], s["duration_s"], note))

    companies = health.get("companies") or {}
    if companies:
        broken = {n: r for n, r in companies.items() if not r["ok"]}
        print("\nCompanies: %d ok, %d failed"
              % (len(companies) - len(broken), len(broken)))
        if broken:
            for name, rec in sorted(broken.items()):
                print("  FAILED  %-22s %s" % (name, rec.get("error", "")[:50]))
        if args.verbose:
            for name, rec in sorted(companies.items()):
                if rec["ok"]:
                    print("  ok      %-22s %d jobs" % (name, rec["jobs"]))

    any_ok = any(s["ok"] for s in health["sources"])
    return 0 if any_ok else 1


SETUP_HELP = """
No TELEGRAM_BOT_TOKEN found. Create a bot first — it takes about a minute:

  1. Open Telegram and message @BotFather
  2. Send:  /newbot
  3. Pick any name, then a username ending in 'bot'
  4. BotFather replies with a token like 8123456789:AAH...xyz

Then put it in .env:

  TELEGRAM_BOT_TOKEN=<paste the token here>

...and run this command again.
""".strip()


def cmd_setup_telegram(args) -> int:
    """Verify the bot token and discover the chat id, then save it to .env.

    The chat id can't be looked up from the token alone — Telegram only reveals
    it once *you* message the bot, which is also what authorises it to message
    you back. So: confirm the token, then read the pending updates.
    """
    settings = Settings.from_env()
    if not settings.telegram_bot_token:
        print(SETUP_HELP)
        return 1

    bot = TelegramNotifier(settings.telegram_bot_token, chat_id="_probe_")
    try:
        me = bot.call("getMe")
    except Exception as exc:  # noqa: BLE001
        print("Token rejected: %s" % exc)
        print("\nDouble-check the value of TELEGRAM_BOT_TOKEN in .env.")
        return 1
    username = me.get("username", "?")
    print("Bot OK: @%s" % username)

    try:
        updates = bot.call("getUpdates", {"timeout": 0, "limit": 20})
    except Exception as exc:  # noqa: BLE001
        print("Could not read updates: %s" % exc)
        return 1

    chats = {}
    for upd in updates or []:
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            chats[str(chat["id"])] = chat

    if not chats:
        print("\nNo messages yet. Open Telegram, send any message to @%s"
              " (for example /start), then run this again." % username)
        return 1

    if len(chats) > 1:
        print("\nSeveral chats have messaged this bot:")
        for cid, chat in chats.items():
            print("  %-16s %s" % (cid, chat.get("first_name") or chat.get("title") or ""))
        print("Set TELEGRAM_CHAT_ID in .env by hand to pick one.")
        return 1

    chat_id, chat = next(iter(chats.items()))
    path = write_env("TELEGRAM_CHAT_ID", chat_id)
    print("Chat id %s (%s) saved to %s"
          % (chat_id, chat.get("first_name") or chat.get("title") or chat.get("type"), path))

    notifier = TelegramNotifier(settings.telegram_bot_token, chat_id)
    try:
        notifier.send("<b>JobTracker is connected</b> ✅\n\n"
                      "New data roles in Israel will show up here.")
    except Exception as exc:  # noqa: BLE001
        print("Saved, but the test message failed: %s" % exc)
        return 1
    print("Test message sent — check Telegram.")
    return 0


def cmd_test_notify(args) -> int:
    settings = Settings.from_env()
    notifier = pick_notifier(settings, dry_run=False)
    if notifier.name == "console":
        print("No delivery channel configured.")
        print("Run `python -m jobtracker setup-telegram` first.")
        return 1
    message = ("<b>JobTracker test</b> ✅\n\nIf you're reading this, delivery "
               "works and you're all set."
               if notifier.digest_style == "telegram" else
               "*JobTracker test* ✅\n\nIf you're reading this, delivery "
               "works and you're all set.")
    try:
        notifier.send(message)
    except Exception as exc:  # noqa: BLE001
        print("FAILED: %s" % exc)
        return 1
    print("Sent via %s — go check." % notifier.name)
    return 0


def cmd_seed(args) -> int:
    """Mark everything that currently matches as seen, without notifying.

    Run once after setup: the boards are full of roles that were posted before
    you started watching, and a first run would deliver all of them at once.
    After seeding you only hear about postings that appear from now on.
    """
    sources = _resolve_sources(args)
    print("Sources: %s\n" % ", ".join(sources))
    report = run(sources, dry_run=True, notify=False)
    store = SeenStore(STATE_FILE)
    fresh = store.new_jobs(report.new_jobs)
    store.mark_seen(fresh)
    store.prune()
    store.save()
    print("\nSeeded %d current match(es) as already-seen (%d tracked in total)."
          % (len(fresh), len(store)))
    print("From now on you'll only be told about newly posted roles.")
    return 0


def cmd_list(args) -> int:
    store = SeenStore(STATE_FILE)
    if not len(store):
        print("No jobs recorded yet. Run `python -m jobtracker run --dry-run` first.")
        return 0
    records = sorted(store._data.items(), key=lambda kv: kv[1].get("first_seen", ""),
                     reverse=True)
    print("%d job(s) already alerted (newest first):\n" % len(store))
    for _, rec in records[:args.limit]:
        print("  %s  %-42s %-22s %s" % (
            rec.get("first_seen", "")[:16], rec.get("title", "")[:42],
            rec.get("company", "")[:22], rec.get("location", "")[:24]))
    return 0


def cmd_match(args) -> int:
    """Explain why a given title/location would or wouldn't alert you."""
    from .models import Job

    matcher = Matcher(load_search())
    job = Job(title=args.title, company="Test Co", location=args.location,
              url="", source="manual")
    ok, reason = matcher.evaluate(job)
    if ok:
        print("MATCH ✅")
        print("  role      : %s" % job.matched_role)
        print("  pattern   : %s" % job.matched_pattern)
        print("  seniority : %s" % (job.seniority or "-"))
        print("  remote    : %s" % job.is_remote)
    else:
        print("NO MATCH ❌")
        print("  reason    : %s" % reason)
    return 0


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="jobtracker",
        description="Telegram alerts for data roles opening in Israel.")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="check sources and send new matches")
    p_run.add_argument("--dry-run", action="store_true",
                       help="print the digest instead of sending; don't touch state")
    p_run.add_argument("--only", help="comma-separated list of sources to use")
    p_run.add_argument("--enable", help="add opt-in sources (%s)" % ",".join(OPT_IN_SOURCES))
    p_run.set_defaults(func=cmd_run)

    p_src = sub.add_parser("sources", help="health-check every source")
    p_src.add_argument("--only")
    p_src.add_argument("--enable")
    p_src.set_defaults(func=cmd_sources)

    p_setup = sub.add_parser("setup-telegram",
                             help="find your chat id and save it to .env")
    p_setup.set_defaults(func=cmd_setup_telegram)

    p_seed = sub.add_parser(
        "seed", help="mark every current match as seen without notifying")
    p_seed.add_argument("--only")
    p_seed.add_argument("--enable")
    p_seed.set_defaults(func=cmd_seed)

    p_test = sub.add_parser("test-notify", help="send a test message")
    p_test.set_defaults(func=cmd_test_notify)

    p_list = sub.add_parser("list", help="show jobs already alerted")
    p_list.add_argument("--limit", type=int, default=40)
    p_list.set_defaults(func=cmd_list)

    p_match = sub.add_parser("match", help="test a title/location against the rules")
    p_match.add_argument("title")
    p_match.add_argument("--location", default="Tel Aviv-Yafo")
    p_match.set_defaults(func=cmd_match)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
