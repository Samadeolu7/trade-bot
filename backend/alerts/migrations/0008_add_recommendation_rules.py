"""People who set up their alerts before the MT5 recommendation feed
existed never got rules for it, so feed calls fired no alert. Adds the
recommendation, near-miss and repeat-signal rules to everyone who already
has alert rules and lacks them."""

from django.db import migrations

ADDED_LATER = ["recommendation", "near_miss", "repeat_signal"]


def add_missing(apps, schema_editor):
    AlertRule = apps.get_model("alerts", "AlertRule")
    users = AlertRule.objects.values_list("user_id", flat=True).distinct()
    for user_id in users:
        have = set(AlertRule.objects.filter(user_id=user_id).values_list("kind", flat=True))
        for kind in ADDED_LATER:
            if kind not in have:
                AlertRule.objects.create(user_id=user_id, kind=kind)


class Migration(migrations.Migration):
    dependencies = [("alerts", "0007_pushkeys_pushsubscription")]
    operations = [migrations.RunPython(add_missing, migrations.RunPython.noop)]
