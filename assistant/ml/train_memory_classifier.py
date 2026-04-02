"""Train a simple binary memory importance classifier from CSV data."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

try:
    import joblib  # type: ignore
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
    from sklearn.linear_model import LogisticRegression  # type: ignore
    from sklearn.pipeline import Pipeline  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Training dependencies missing. Install scikit-learn and joblib to train memory classifier."
    ) from exc


def _parse_label(value: str) -> int:
    normalized = str(value or "").strip().lower()
    return 1 if normalized in {"1", "true", "yes", "keep", "important"} else 0


def train(csv_path: Path, output_path: Path) -> None:
    x: list[str] = []
    y: list[int] = []
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "message" not in reader.fieldnames or "keep" not in reader.fieldnames:
            raise RuntimeError("Training CSV must contain headers: message,keep")
        for row in reader:
            message = str(row.get("message", "") or "").strip()
            if not message:
                continue
            x.append(message)
            y.append(_parse_label(str(row.get("keep", ""))))
    if not x:
        raise RuntimeError("Training CSV is empty after parsing.")

    pipeline = Pipeline(
        [
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=1)),
            ("clf", LogisticRegression(max_iter=500)),
        ]
    )
    pipeline.fit(x, y)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, output_path)
    print(f"Saved model to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Path to training CSV with columns message,keep")
    parser.add_argument("--out", required=True, help="Output .joblib model path")
    args = parser.parse_args()
    train(Path(args.csv).expanduser().resolve(), Path(args.out).expanduser().resolve())


if __name__ == "__main__":
    main()
