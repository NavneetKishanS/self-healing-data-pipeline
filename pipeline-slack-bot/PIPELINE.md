# Pipeline Slack integration

The bot uses Socket Mode and runs the existing pipeline backend on port 8001 in
the same process. Do not start a second `python -m agent serve` alongside it.

Install dependencies with `.venv/bin/pip install -r requirements.txt` from this folder.
Configure this folder's ignored `.env` with `SLACK_CHANNEL_ID`, `SLACK_TEAM_ID`,
`SLACK_APP_ID`, and comma-separated `SLACK_APPROVER_IDS`. Slack CLI supplies the
bot and app tokens at runtime. The repo-root `.env` supplies the model credentials.

Run `slack run app.py` here. Run `npm --prefix frontend run dev` from the repo root.
Invite the bot to the configured channel. No public tunnel is needed.

New schema drift posts a summary (at most one every 30 seconds during replay).
Click Investigate on a flagged batch in the dashboard to start model diagnosis.
When critique permits a repair, Slack displays its exact proposal with Approve
and Reject buttons. Only configured reviewers in the configured workspace and
channel can decide. Expired, changed, duplicate and previously decided proposals
are refused. Browser approval still works; first valid decision wins.
The final outcome replaces the Slack approval message and removes its buttons.

Slack sending failures never authorize a repair. A server restart does not resume
pending investigations. This bridge intentionally supports local demo ownership
only; Auth0 deployments need an explicit mapping of reviewers to incident owners.
Raw rows are not posted. Diagnosis and repair proposals are shared with the channel.
