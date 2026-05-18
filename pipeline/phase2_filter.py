"""Phase 2: ASReview active learning filter."""
from __future__ import annotations
# __APP_PATHS_INSTALLED__
from app_paths import app_data, resource

import json
from pathlib import Path

import pandas as pd
from rich.console import Console

from utils.checkpointing import Checkpoint

console = Console()


def jsonl_to_asreview_csv(jsonl_path: Path, csv_path: Path) -> None:
    rows = []
    with jsonl_path.open(encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)
            rows.append({
                "id": p["id"],
                "title": p["title"],
                "abstract": p["abstract"],
                "authors": ";".join(p.get("authors", [])),
                "year": p.get("year"),
            })
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    console.print(f"Exported {len(rows)} papers to {csv_path}")


def run() -> None:
    checkpoint = Checkpoint("phase2_filter")
    if checkpoint.is_complete():
        console.print("[green]Phase 2 already complete. Skipping.[/]")
        return

    console.print("[bold cyan]Phase 2: ASReview filtering[/]")

    jsonl_path = app_data("data/raw/papers.jsonl")
    csv_path = app_data("data/filtered/papers_for_asreview.csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    if not jsonl_path.exists():
        console.print("[red]No papers.jsonl found — run Phase 1 first.[/]")
        return

    jsonl_to_asreview_csv(jsonl_path, csv_path)

    console.print(
        "\n[yellow]Next steps:[/]\n"
        "  1. Run: [bold]asreview lab[/]\n"
        "  2. Upload: data/filtered/papers_for_asreview.csv\n"
        "  3. Label ~50 relevant + ~50 irrelevant papers\n"
        "  4. Export labelled CSV to: data/filtered/labelled.csv\n"
        "  5. Re-run pipeline (Phase 2 will detect the labelled file)\n"
    )

    labelled_path = app_data("data/filtered/labelled.csv")
    if labelled_path.exists():
        df = pd.read_csv(labelled_path)
        relevant = df[df.get("included", df.get("label_included", pd.Series())) == 1]
        console.print(f"Found {len(relevant)} relevant papers from ASReview labels")

        relevant_ids = set(relevant["id"].astype(str))
        out_path = app_data("data/filtered/relevant_papers.jsonl")
        count = 0
        with jsonl_path.open(encoding="utf-8") as fin, out_path.open("w", encoding="utf-8") as fout:
            for line in fin:
                p = json.loads(line)
                if p["id"] in relevant_ids:
                    fout.write(line)
                    count += 1
        console.print(f"Wrote {count} relevant papers to {out_path}")
        checkpoint.mark_complete()
    else:
        console.print("[yellow]Labelled CSV not found — Phase 2 paused (manual step required).[/]")


if __name__ == "__main__":
    run()