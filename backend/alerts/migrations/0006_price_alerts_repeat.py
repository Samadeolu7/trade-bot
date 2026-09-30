from django.db import migrations


def repeat(apps, schema_editor):
    """Price-cross alerts used to be created as "only once" and switched
    themselves off after firing. They now keep working until their owner
    turns them off: the ones switched off by firing are switched back on
    (with fresh state, so an old reading can't trigger a false cross), and
    all of them get a 15-minute pause between repeats."""
    AlertRule = apps.get_model("alerts", "AlertRule")
    rules = AlertRule.objects.filter(kind__in=["price_above", "price_below"], once=True)
    for rule in rules:
        if not rule.enabled and rule.last_fired_at is not None:
            rule.enabled = True
        rule.once = False
        rule.cooldown_minutes = max(rule.cooldown_minutes, 15)
        rule.state = {}
        rule.save()


class Migration(migrations.Migration):
    dependencies = [("alerts", "0005_alertevent_dismissed_at")]

    operations = [migrations.RunPython(repeat, migrations.RunPython.noop)]
