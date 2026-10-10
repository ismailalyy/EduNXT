"""Command line: serve | chat | render-sample | check-setup."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import load_settings, normalize_phone

SIM_CLIENT = "201000000001"
SIM_FOUNDER = "201999999999"


def cmd_serve(args) -> None:
    import uvicorn

    from .app import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port)


def cmd_render_sample(args) -> None:
    from .documents.render import render_pdfs
    from .sample import sample_case

    settings = load_settings()
    profile, draft = sample_case(settings)
    for path in render_pdfs(profile, draft, settings.output_dir / "sample", "EN-0000", 1):
        print(path)


def cmd_check_setup(args) -> None:
    from .setup_check import check_setup

    sys.exit(0 if check_setup(load_settings(), normalize_phone(args.send_test) or None) else 1)


def cmd_benchmark(args) -> None:
    from .agents.intake import IntakeAgent
    from .agents.llm import make_client
    from .app import build_drafter, build_researcher
    from .benchmark import run_benchmark
    from .documents.render import render_pdfs

    settings = load_settings()
    client = make_client(settings.anthropic_api_key)
    intake = IntakeAgent(client, settings.intake_model)

    render = render_pdfs
    try:
        import weasyprint  # noqa: F401  (needs Pango, which Windows lacks by default)
    except (ImportError, OSError) as exc:
        print(f"⚠️  PDF rendering unavailable ({type(exc).__name__}); measuring cost and quality without PDFs.\n")

        def render(*_args, **_kwargs):
            return None

    def make_drafter(universities_csv):
        return build_drafter(settings, client, universities_csv)

    def make_researcher(universities_csv, scholarships_csv, log_path):
        return build_researcher(settings, client, universities_csv, scholarships_csv, log_path)

    out = run_benchmark(
        settings,
        intake,
        make_drafter,
        render,
        case_ids=args.cases.split(",") if args.cases else None,
        parallel=args.parallel,
        make_researcher=make_researcher if args.research else None,
    )
    print((out / "summary.md").read_text(encoding="utf-8"))
    print(f"\nFull results: {out}")


def cmd_chat(args) -> None:
    """Play client and founder in the terminal; the full workflow runs with a simulated WhatsApp."""
    from .app import build_workflow
    from .channels.base import InboundMessage
    from .channels.simulator import SimulatorChannel

    settings = load_settings()
    if not settings.anthropic_api_key:
        print("⚠️  ANTHROPIC_API_KEY is not set: client messages and drafts will fail. See docs/SETUP.md step A.\n")
    settings.database_url = f"sqlite:///{settings.output_dir / 'simulator.db'}"
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    founder = settings.founder_numbers[0] if settings.founder_numbers else SIM_FOUNDER
    settings.founder_numbers = [founder]
    client_phone = SIM_CLIENT
    channel = SimulatorChannel(
        labels={founder: f"FOUNDER +{founder}", client_phone: f"CLIENT +{client_phone}"},
        inbox_dir=settings.output_dir / "simulator_inbox",
    )
    workflow = build_workflow(settings, channel)
    role = args.role
    counter = 0
    print(
        "EduNXT simulator. Type a message and press Enter.\n"
        "  /client or /founder  switch who you are (now: %s)\n"
        "  /newclient           act as a different, new client\n"
        "  /reply <id> <text>   swipe-reply to an outgoing message id (e.g. sim.out.3)\n"
        "  /quit\n" % role
    )
    while True:
        try:
            line = input(f"[{role}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in ("/quit", "/exit"):
            return
        if line in ("/client", "/founder"):
            role = line[1:]
            continue
        if line == "/newclient":
            client_phone = str(int(client_phone) + 1)
            channel.labels[client_phone] = f"CLIENT +{client_phone}"
            role = "client"
            print(f"Now client +{client_phone}")
            continue
        reply_to = None
        if line.startswith("/reply "):
            _, reply_to, line = line.split(" ", 2)
        counter += 1
        phone = founder if role == "founder" else client_phone
        workflow.receive(InboundMessage(phone=phone, text=line, message_id=f"sim.in.{counter}.{id(line)}", reply_to_id=reply_to))
        workflow.run_pending(("inbound",))
        if workflow.store.has_queued(("draft", "revise")):
            print("⏳ researching (this calls the live API and can take a few minutes)…")
            workflow.run_pending()


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("weasyprint", "fontTools", "httpx"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(prog="edunxt")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the webhook server and worker")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("chat", help="simulate WhatsApp in the terminal")
    p.add_argument("--as", dest="role", choices=["client", "founder"], default="client")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("benchmark", help="run real cases and measure cost, time and overlap with consultants")
    p.add_argument("--cases", default="", help="comma-separated case ids (default: all)")
    p.add_argument("--research", action="store_true", help="measure the Research Agent (list updates) instead of per-student drafts")
    p.add_argument("--parallel", type=int, default=2, help="cases to run at once")
    p.set_defaults(func=cmd_benchmark)

    p = sub.add_parser("render-sample", help="render the example case PDFs")
    p.set_defaults(func=cmd_render_sample)

    p = sub.add_parser("check-setup", help="verify credentials and configuration")
    p.add_argument("--send-test", default="", help="also send WhatsApp's hello_world template to this number")
    p.set_defaults(func=cmd_check_setup)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
