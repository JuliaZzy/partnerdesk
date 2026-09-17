"""Email-agent LLM calls, spawned by Node as `python -m email_agent.tasks.<name>`.

Node owns the database, email sending, and scheduling; this package owns the
model calls (compose, inbound-reply triage, lead research, business-card vision).
Mirrors the split already used by contract_ai and brand_knowledge — there is no
service to run.
"""
