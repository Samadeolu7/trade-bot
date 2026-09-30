from django.core.management.base import BaseCommand

from trading.engine.loop import Engine


class Command(BaseCommand):
    help = "Run the trading engine: bots, engine-held stops, resting paper orders, equity snapshots."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="run a single tick (and bar poll) and exit")

    def handle(self, *args, once=False, **options):
        engine = Engine()
        if once:
            engine.last_bar_poll = -engine.bar_poll_seconds
            engine.tick()
            return
        engine.run_forever()
