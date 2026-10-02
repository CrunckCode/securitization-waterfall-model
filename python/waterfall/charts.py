"""Charts written to outputs/charts."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from . import config as cfg
from . import waterfall as wf
from .waterfall import col

SUBORDINATION_PCT = {"A": 25.0, "B": 15.0, "C": 7.5}   # principal below each class, % of original pool


def paydown(dfs, path):
    fig, axes = plt.subplots(1, len(dfs), figsize=(11, 4), sharey=True)
    for ax, (name, df) in zip(axes, dfs.items()):
        for c in cfg.CLASSES:
            ax.plot(df[wf.PERIOD], df[col("bal", c)] / 1e6, label=f"Class {c}")
        ax.set_title(name)
        ax.set_xlabel("Month")
    axes[0].set_ylabel("Balance (USD m)")
    axes[0].legend()
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def loss_vs_subordination(dfs, deal, path):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for name, df in dfs.items():
        ax.plot(df[wf.PERIOD], df[wf.CUM_LOSS] / deal.pool_balance * 100, label=name)
    for c, v in SUBORDINATION_PCT.items():
        ax.axhline(v, ls="--", color="grey", lw=0.8)
        ax.text(1, v + 0.3, f"Class {c} subordination {v:.1f}%", fontsize=7)
    ax.set_xlabel("Month")
    ax.set_ylabel("Cumulative net loss (% of original pool)")
    ax.set_title("Cumulative net loss vs principal subordination (reserve excluded)")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def breakeven(table, path):
    fig, ax = plt.subplots(figsize=(6, 4))
    width = 0.35
    cols = list(table.columns)
    for i, sev in enumerate(table.index):
        ax.bar([x + i * width for x in range(len(cols))], table.loc[sev] * 100, width, label=f"Severity {sev:.0%}")
    ax.set_xticks([x + width / 2 for x in range(len(cols))])
    ax.set_xticklabels(cols)
    ax.set_ylabel("Break-even annual CDR (%)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def mc_loss(table, path):
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(table.index, table["prob_loss"] * 100)
    ax.set_ylabel("Probability of any shortfall (%)")
    ax.set_title("Monte Carlo: shortfall probability by class")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
