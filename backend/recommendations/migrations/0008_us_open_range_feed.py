from django.db import migrations

NAME = "us_open_range_15m"


def create_feed(apps, schema_editor):
    """Forward paper test of hypothesis #7's best result (notes/research_log.md,
    2026-10-31). Not followed: tracked in the app, no trade alerts. Costs
    0.01% per side, the 0.02% round trip the backtest was judged at."""
    Feed = apps.get_model("recommendations", "Feed")
    Feed.objects.get_or_create(name=NAME, defaults={
        "strategy": "us_open_range", "params": {}, "symbol": "BTC/USDT", "timeframe": "15m",
        "fee": 0.0, "slippage": 0.0001, "following": False,
        # Exness BTCUSDc: 1 lot = 0.01 BTC, 0.01 minimum and step
        "contract_size": 0.01, "min_lot": 0.01, "lot_step": 0.01,
    })


def remove_feed(apps, schema_editor):
    apps.get_model("recommendations", "Feed").objects.filter(name=NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("recommendations", "0007_following"),
    ]

    operations = [
        migrations.RunPython(create_feed, remove_feed),
    ]
