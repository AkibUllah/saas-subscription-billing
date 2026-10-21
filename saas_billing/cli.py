"""`billing` command line: run billing, show metrics, seed demo data."""

from __future__ import annotations

import json
from datetime import datetime

import typer

from saas_billing.clock import ensure_utc, utcnow
from saas_billing.config import get_settings
from saas_billing.core.money import format_money
from saas_billing.database import init_db, make_engine, make_session_factory
from saas_billing.services import billing_run, metrics
from saas_billing.services.context import BillingContext

app = typer.Typer(help="SaaS subscription billing engine", no_args_is_help=True)


def _session_and_ctx():
    settings = get_settings()
    engine = make_engine(settings.database_url)
    if settings.database_url.startswith("sqlite"):
        init_db(engine)
    return make_session_factory(engine)(), BillingContext.from_settings(settings)


@app.command()
def run(
    as_of: datetime | None = typer.Option(None, help="ISO timestamp; default now (UTC)"),
) -> None:
    """Process every subscription and dunning retry due at AS_OF."""
    session, ctx = _session_and_ctx()
    with session:
        result = billing_run.run_billing(session, ctx, ensure_utc(as_of) if as_of else utcnow())
        typer.echo(
            f"run {result.id}: subscriptions={result.subscriptions_processed} "
            f"invoices={result.invoices_created} paid={result.payments_succeeded} "
            f"failed={result.payments_failed} transitions={json.dumps(result.transitions)}"
        )


@app.command(name="metrics")
def metrics_cmd(
    as_of: datetime | None = typer.Option(None), window_days: int = typer.Option(30, min=1, max=366)
) -> None:
    """Print MRR, ARR, churn and trial conversion."""
    session, _ = _session_and_ctx()
    with session:
        m = metrics.compute_metrics(session, ensure_utc(as_of) if as_of else utcnow(), window_days)
        currency = get_settings().default_currency
        typer.echo(f"MRR               {format_money(m.mrr_minor, currency)}")
        typer.echo(f"ARR               {format_money(m.arr_minor, currency)}")
        typer.echo(
            f"Active            {m.active_subscriptions} (past due {m.past_due_subscriptions})"
        )
        typer.echo(f"Trialing          {m.trialing_subscriptions}")
        typer.echo(
            f"Churn ({m.window_days}d)        {m.churned_in_window}/"
            f"{m.active_at_window_start + m.new_paying_in_window} = {m.churn_rate:.1%}"
        )
        typer.echo(
            f"Trial conversion  {m.trials_converted}/{m.trials_ended}"
            f" = {m.trial_conversion_rate:.1%}"
        )


@app.command()
def seed() -> None:
    """Create demo plans, a coupon and customers (idempotent on codes/emails)."""
    from saas_billing.demo import seed_demo

    session, ctx = _session_and_ctx()
    with session:
        created = seed_demo(session, ctx, ensure_utc(ctx.clock.now()))
        session.commit()
        typer.echo(f"seeded: {json.dumps(created)}")


if __name__ == "__main__":  # pragma: no cover
    app()
