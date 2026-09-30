"""Rebuilds the research reports run by the CLI (the Research Report
workflow, which posted them to Telegram) from their experiment rows, so
they read in the app like reports run here. The CLI records one experiment
per window per variant, a few seconds apart; a report is a burst of those."""

from datetime import timedelta

from research.models import Experiment

GAP = timedelta(minutes=3)
KINDS = ("research_report", "holdout_check")


def _flatten(config: dict) -> dict:
    return {f"{section}.{key}": value for section, values in config.items() if isinstance(values, dict)
            for key, value in values.items()}


def _window(e: Experiment) -> str:
    if e.kind == "holdout_check":
        return "holdout"
    return "train" if e.window_start[:4] < "2024" else "test"


def legacy_reports() -> list[dict]:
    rows = list(Experiment.objects.filter(kind__in=KINDS, source_id__isnull=False).order_by("created_at"))
    groups: list[list[Experiment]] = []
    for e in rows:
        last = groups[-1][-1] if groups else None
        if last and e.created_at - last.created_at <= GAP and (e.symbol, e.timeframe, e.kind) == (
            last.symbol, last.timeframe, last.kind
        ):
            groups[-1].append(e)
        else:
            groups.append([e])

    reports = []
    for group in groups:
        runs: dict[tuple, dict] = {}
        for e in group:
            run = runs.setdefault((e.strategy, e.config_hash), {"strategy": e.strategy, "config": e.config,
                                                                "windows": {}})
            run["windows"][_window(e)] = e.result
        main_strategy = group[0].strategy
        ordered = list(runs.values())
        for run in ordered:
            siblings = [r for r in ordered if r["strategy"] == run["strategy"]]
            flat = _flatten(run["config"])
            differing = sorted(
                k for k in flat
                if any(_flatten(s["config"]).get(k) != flat[k] for s in siblings if s is not run)
            )
            params = ", ".join(f"{k.split('.', 1)[1]}={flat[k]}" for k in differing)
            run["baseline"] = run["strategy"] != main_strategy
            run["label"] = (f"baseline: {run['strategy']}" if run["baseline"]
                            else f"{run['strategy']} {params}" if params else f"{run['strategy']} (defaults)")
            run["equity"] = {}
        for run in ordered:
            del run["config"]
        reports.append({
            "id": group[0].pk,
            "created_at": group[0].created_at,
            "kind": group[0].kind,
            "header": {"strategy": main_strategy, "symbol": group[0].symbol, "timeframe": group[0].timeframe},
            "runs": sorted(ordered, key=lambda r: r["baseline"]),
        })
    return list(reversed(reports))
