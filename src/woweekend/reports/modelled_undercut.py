"""Report presentation of the strategy model's tyre-only undercut curve."""
from pathlib import Path

from wostrategy.analysis.modelled_undercut import modelled_undercut_curve

FIGURE = "modelled_undercut_power.png"


def save_modelled_undercut(run, model, max_age):
    """Use the caller's effective model directly; never load alternate tyre inputs."""
    result = modelled_undercut_curve(model, max_age)
    result["age_range_basis"] = "0 through report race-distance horizon; axis is model tyre age, not race lap"
    run.save_json("modelled_undercut_power", result)
    path = save_figure(result, run.path / "figures" / FIGURE)
    return result, path


def save_figure(result, path):
    from matplotlib.figure import Figure

    compounds = sorted(result["model"]["compound_delta"])
    fig = Figure(figsize=(5 * len(compounds), 4.5), constrained_layout=True)
    axes = fig.subplots(1, len(compounds), squeeze=False, sharey=True)[0]
    for ax, source in zip(axes, compounds):
        for replacement in compounds:
            rows = [p for p in result["points"] if p["source_compound"] == source
                    and p["replacement_compound"] == replacement]
            ax.plot([p["source_tyre_age"] for p in rows],
                    [p["modelled_undercut_power_seconds"] for p in rows],
                    label=f"Fresh {replacement}")
        ax.axhline(0, color="grey", linewidth=.8)
        ax.set_title(f"Source: {source}")
        ax.set_xlabel("Source model tyre age")
        ax.grid(alpha=.2)
        ax.legend(fontsize=8)
    axes[0].set_ylabel("Modelled undercut power (s)\nPositive = fresh replacement faster")
    fig.suptitle(result["title"] + "\nFresh replacement: theoretical model age-zero anchor", fontsize=12)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    return path


def undercut_markdown(component):
    result = component.get("output") or {}
    if not result:
        return "## Modelled / predicted undercut power\n\nUnavailable for this run.\n"
    return (
        "## Modelled / predicted undercut power\n\n"
        "Source modelled tyre pace at age a minus fresh replacement modelled tyre pace at age 0. "
        "Positive means the replacement is faster; negative means slower. "
        "The fresh reference is the theoretical model age-zero anchor, not an observed out-lap. "
        "The axis is model tyre age, not race lap. All ordered compound transitions, including "
        "same-compound replacements, use the effective strategy model for this run. "
        "This is distinct from Live Observed undercut estimate and uses no Live measurements.\n\n"
        "Authoritative points and model parameters: `analysis_results.json`, "
        "`modelled_undercut_power.output`; run artifact: `modelled_undercut_power.json`.\n\n"
        f"![Modelled / predicted undercut power](figures/{FIGURE})\n"
    )
