"""Run the local pipeline HTTP server and its authenticated Slack socket together."""
import logging
import os
from pathlib import Path
import sys
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from werkzeug.serving import make_server
from agent.settings import load_settings
from agent.server import create_app
from agent.slack import SlackNotifier


def main():
    logging.basicConfig(level=logging.WARNING)
    load_settings()
    load_dotenv(Path(__file__).with_name(".env"), override=False)
    notifier = SlackNotifier()
    if not notifier.enabled:
        raise SystemExit("Configure SLACK_CHANNEL_ID, SLACK_TEAM_ID, SLACK_APP_ID and SLACK_APPROVER_IDS. Start with slack run for credentials.")
    bolt = App(token=os.environ["SLACK_BOT_TOKEN"])
    backend = create_app(local_no_auth=True, slack=notifier)

    @bolt.action("pipeline_approve")
    @bolt.action("pipeline_reject")
    def decide(ack, body, respond):
        ack()
        result, status = backend.extensions["slack_action"](body)
        respond(text=result["text"], response_type="ephemeral", replace_original=False)
        print(f"Slack decision processed: HTTP {status}", flush=True)

    server = make_server("127.0.0.1", int(os.getenv("AGENT_PORT", "8001")), backend, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    print("Pipeline backend and Slack approval bridge ready on http://127.0.0.1:8001", flush=True)
    try:
        SocketModeHandler(bolt, os.environ["SLACK_APP_TOKEN"]).start()
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
