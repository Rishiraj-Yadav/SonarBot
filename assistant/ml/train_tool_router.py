"""Train a simple multi-label tool router model from CSV data."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

try:
    import joblib  # type: ignore
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
    from sklearn.linear_model import LogisticRegression  # type: ignore
    from sklearn.multiclass import OneVsRestClassifier  # type: ignore
    from sklearn.pipeline import Pipeline  # type: ignore
    from sklearn.preprocessing import MultiLabelBinarizer  # type: ignore
except Exception as exc:  # pragma: no cover - runtime dependency guard
    raise RuntimeError(
        "Training dependencies missing. Install scikit-learn and joblib to train tool router."
    ) from exc


def _parse_tools(value: str) -> list[str]:
    compact = str(value or "").strip()
    if not compact:
        return []
    try:
        parsed = json.loads(compact)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    delimiter = "|" if "|" in compact else ","
    return [item.strip() for item in compact.split(delimiter) if item.strip()]


def train(csv_path: Path, output_path: Path) -> None:
    x: list[str] = []
    labels: list[list[str]] = []
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "message" not in reader.fieldnames or "tools" not in reader.fieldnames:
            raise RuntimeError("Training CSV must contain headers: message,tools")
        for row in reader:
            message = str(row.get("message", "") or "").strip()
            tools_raw = str(row.get("tools", "") or "")
            if not message:
                continue
            x.append(message)
            labels.append(_parse_tools(tools_raw))
    if not x:
        raise RuntimeError("Training CSV is empty after parsing.")

    mlb = MultiLabelBinarizer()
    y = mlb.fit_transform(labels)

    estimator = OneVsRestClassifier(LogisticRegression(max_iter=500))
    pipeline = Pipeline(
        [
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=1)),
            ("clf", estimator),
        ]
    )
    pipeline.fit(x, y)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {"pipeline": pipeline, "labels": mlb.classes_.tolist()}
    joblib.dump(artifact, output_path)
    print(f"Saved model to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Path to training CSV with columns message,tools")
    parser.add_argument("--out", required=True, help="Output .joblib model path")
    args = parser.parse_args()
    train(Path(args.csv).expanduser().resolve(), Path(args.out).expanduser().resolve())


if __name__ == "__main__":
    main()
