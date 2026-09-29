import json

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Print the API's OpenAPI schema; the frontend generates its typed client from it."

    def handle(self, *args, **options):
        from config.api import api

        self.stdout.write(json.dumps(api.get_openapi_schema(), indent=2, sort_keys=True))
