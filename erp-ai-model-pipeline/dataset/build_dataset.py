"""Synthetic Dataset Builder for ERP Text-to-SQL Fine-Tuning (Feature 28).

Generates 3,000 hybrid ChatML JSONL samples:
- 2,100 samples (70%): BIRD-SQL converted (evidence -> calculated_metrics)
- 600 samples (20%): Gretel synthetic ERP multi-table queries
- 300 samples (10%): Adversarial security refusal pairs

Splits deterministically into:
- train.jsonl (2,700 samples, 90%)
- val.jsonl (300 samples, 10%)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from dataset.bird_converter import generate_bird_samples
from dataset.erp_synthetic import generate_erp_synthetic_samples
from dataset.refusal_generator import generate_refusal_samples
from dataset.validator import validate_dataset_records

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def build_dataset(
    total_samples: int = 3000,
    bird_ratio: float = 0.70,
    gretel_ratio: float = 0.20,
    refusal_ratio: float = 0.10,
    split_ratio: float = 0.90,
    seed: int = 42,
    output_dir: Path | str = Path("data"),
    bird_file: Optional[Path | str] = None,
) -> dict[str, Any]:
    """Generate, validate, and write the 3,000 hybrid ChatML dataset."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    n_bird = int(total_samples * bird_ratio)
    n_gretel = int(total_samples * gretel_ratio)
    n_refusal = total_samples - n_bird - n_gretel

    logger.info("Generating %d BIRD-SQL mapped samples (evidence -> calculated_metrics)...", n_bird)
    bird_samples = generate_bird_samples(count=n_bird, seed=seed, source_file=Path(bird_file) if bird_file else None)

    logger.info("Generating %d Gretel synthetic multi-table ERP samples...", n_gretel)
    gretel_samples = generate_erp_synthetic_samples(count=n_gretel, seed=seed + 1)

    logger.info("Generating %d adversarial security refusal pairs...", n_refusal)
    refusal_samples = generate_refusal_samples(count=n_refusal, seed=seed + 2)

    all_samples = bird_samples + gretel_samples + refusal_samples
    logger.info("Total generated samples: %d. Running strict ChatML and AST validation...", len(all_samples))

    report = validate_dataset_records(all_samples)
    if not report["is_valid"]:
        logger.error("Dataset validation failed with %d errors: %s", report["invalid_records"], report["errors"])
        raise ValueError(f"Dataset validation failed: {report['errors']}")
    logger.info("Validation passed! 100%% of records are valid ChatML and ReportPlanSchema.")

    # Stratified 90/10 split
    n_train_bird = int(n_bird * split_ratio)
    train_bird = bird_samples[:n_train_bird]
    val_bird = bird_samples[n_train_bird:]

    n_train_gretel = int(n_gretel * split_ratio)
    train_gretel = gretel_samples[:n_train_gretel]
    val_gretel = gretel_samples[n_train_gretel:]

    n_train_refusal = int(n_refusal * split_ratio)
    train_refusal = refusal_samples[:n_train_refusal]
    val_refusal = refusal_samples[n_train_refusal:]

    train_data = train_bird + train_gretel + train_refusal
    val_data = val_bird + val_gretel + val_refusal

    train_path = out_dir / "train.jsonl"
    val_path = out_dir / "val.jsonl"

    logger.info("Writing %d training samples to %s...", len(train_data), train_path)
    with open(train_path, "w", encoding="utf-8") as f:
        for item in train_data:
            f.write(json.dumps(item) + "\n")

    logger.info("Writing %d validation samples to %s...", len(val_data), val_path)
    with open(val_path, "w", encoding="utf-8") as f:
        for item in val_data:
            f.write(json.dumps(item) + "\n")

    summary = {
        "total_samples": len(all_samples),
        "train_samples": len(train_data),
        "val_samples": len(val_data),
        "bird_count": n_bird,
        "gretel_count": n_gretel,
        "refusal_count": n_refusal,
        "train_path": str(train_path),
        "val_path": str(val_path),
        "validation_report": {
            "is_valid": report["is_valid"],
            "total_records": report["total_records"],
            "valid_records": report["valid_records"],
        },
    }

    # Save summary metadata file
    meta_path = out_dir / "dataset_summary.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    logger.info("Dataset build complete! Summary written to %s", meta_path)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic Dataset Builder for ERP Text-to-SQL")
    parser.add_argument("--total-samples", type=int, default=3000, help="Total sample count (default: 3000)")
    parser.add_argument("--bird-ratio", type=float, default=0.70, help="BIRD-SQL ratio (default: 0.70)")
    parser.add_argument("--gretel-ratio", type=float, default=0.20, help="Gretel ERP ratio (default: 0.20)")
    parser.add_argument("--refusal-ratio", type=float, default=0.10, help="Adversarial refusal ratio (default: 0.10)")
    parser.add_argument("--split-ratio", type=float, default=0.90, help="Train/val split ratio (default: 0.90)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--output-dir", type=Path, default=Path("data"), help="Output directory")
    parser.add_argument("--bird-file", type=Path, default=None, help="Optional raw BIRD-SQL JSON file")
    parser.add_argument("--offline", action="store_true", default=True, help="Force offline procedural synthesis")

    args = parser.parse_args()

    try:
        summary = build_dataset(
            total_samples=args.total_samples,
            bird_ratio=args.bird_ratio,
            gretel_ratio=args.gretel_ratio,
            refusal_ratio=args.refusal_ratio,
            split_ratio=args.split_ratio,
            seed=args.seed,
            output_dir=args.output_dir,
            bird_file=args.bird_file,
        )
        print(f"\nSuccessfully generated {summary['total_samples']} samples:")
        print(f"  - Train: {summary['train_samples']} samples ({summary['train_path']})")
        print(f"  - Val:   {summary['val_samples']} samples ({summary['val_path']})")
        print(f"  - Distribution: {summary['bird_count']} BIRD (70%), {summary['gretel_count']} Gretel (20%), {summary['refusal_count']} Refusals (10%)")
        sys.exit(0)
    except Exception as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
