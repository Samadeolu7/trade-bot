from django.db import migrations


def enable(apps, schema_editor):
    """People who have set up alerts get the new "entry signal while in a
    trade" alert switched on; they can turn it off on the Alerts page."""
    AlertRule = apps.get_model("alerts", "AlertRule")
    for user_id in set(AlertRule.objects.values_list("user_id", flat=True)):
        if not AlertRule.objects.filter(user_id=user_id, kind="repeat_signal").exists():
            AlertRule.objects.create(user_id=user_id, kind="repeat_signal")


class Migration(migrations.Migration):
    dependencies = [("alerts", "0003_alter_alertrule_kind")]

    operations = [migrations.RunPython(enable, migrations.RunPython.noop)]
