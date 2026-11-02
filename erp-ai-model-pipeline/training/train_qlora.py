"""Unsloth QLoRA Fine-Tuning Script for Qwen2.5-Coder-7B-Instruct (Feature 29).

Fine-tunes Qwen/Qwen2.5-Coder-7B-Instruct using:
- 4-bit NormalFloat (nf4) quantization
- LoRA rank r=32, alpha=64 on all linear modules
- Cosine learning rate schedule with lr=2e-4 over 3 epochs
- Context window: max_seq_length=4096
- Cross-platform --dry-run mode for macOS / CPU CI testing without CUDA crashes
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any

from training.config import QLoRAConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def estimate_token_count(text: str) -> int:
    """Heuristic token counter (~3.5 chars per token for code/JSON) if tokenizer is absent."""
    if not text:
        return 0
    # Average across words and character length
    words = len(text.split())
    chars = len(text)
    return max(words, int(chars / 3.5))


def run_dry_run_verification(config: QLoRAConfig) -> dict[str, Any]:
    """Execute dry-run validation of dataset, sequence lengths, and training schedule."""
    logger.info("Executing QLoRA fine-tuning in --dry-run mode on %s...", sys.platform)

    train_path = Path(config.train_file)
    val_path = Path(config.val_file)

    if not train_path.exists():
        raise FileNotFoundError(f"Training dataset not found: {train_path}")
    if not val_path.exists():
        raise FileNotFoundError(f"Validation dataset not found: {val_path}")

    # Load and check token lengths
    train_tokens: list[int] = []
    with open(train_path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            data = json.loads(line)
            full_text = " ".join(m.get("content", "") for m in data.get("messages", []))
            n_tokens = estimate_token_count(full_text)
            if n_tokens > config.max_seq_length:
                raise ValueError(
                    f"Sample {idx} in {train_path} exceeds max_seq_length ({n_tokens} > {config.max_seq_length})"
                )
            train_tokens.append(n_tokens)

    val_tokens: list[int] = []
    with open(val_path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            data = json.loads(line)
            full_text = " ".join(m.get("content", "") for m in data.get("messages", []))
            n_tokens = estimate_token_count(full_text)
            if n_tokens > config.max_seq_length:
                raise ValueError(
                    f"Sample {idx} in {val_path} exceeds max_seq_length ({n_tokens} > {config.max_seq_length})"
                )
            val_tokens.append(n_tokens)

    n_train = len(train_tokens)
    n_val = len(val_tokens)
    eff_batch = config.per_device_train_batch_size * config.gradient_accumulation_steps
    steps_per_epoch = math.ceil(n_train / max(eff_batch, 1))
    total_steps = steps_per_epoch * config.num_train_epochs
    warmup_steps = int(total_steps * config.warmup_ratio)

    logger.info("Dataset verified: %d train samples, %d val samples.", n_train, n_val)
    logger.info(
        "Token lengths: Train max=%d, avg=%.1f | Val max=%d, avg=%.1f (limit=%d)",
        max(train_tokens) if train_tokens else 0,
        sum(train_tokens) / max(n_train, 1),
        max(val_tokens) if val_tokens else 0,
        sum(val_tokens) / max(n_val, 1),
        config.max_seq_length,
    )
    logger.info(
        "Training schedule: %d epochs x %d steps/epoch = %d total steps (warmup=%d, eff_batch=%d)",
        config.num_train_epochs,
        steps_per_epoch,
        total_steps,
        warmup_steps,
        eff_batch,
    )

    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "status": "DRY_RUN_PASSED",
        "model_name": config.model_name,
        "load_in_4bit": config.load_in_4bit,
        "quant_type": config.quant_type,
        "lora_r": config.lora_r,
        "lora_alpha": config.lora_alpha,
        "target_modules": config.target_modules,
        "learning_rate": config.learning_rate,
        "lr_scheduler_type": config.lr_scheduler_type,
        "num_train_epochs": config.num_train_epochs,
        "per_device_train_batch_size": config.per_device_train_batch_size,
        "gradient_accumulation_steps": config.gradient_accumulation_steps,
        "effective_batch_size": eff_batch,
        "total_training_samples": n_train,
        "total_validation_samples": n_val,
        "total_training_steps": total_steps,
        "warmup_steps": warmup_steps,
        "max_seq_length": config.max_seq_length,
        "train_token_max": max(train_tokens) if train_tokens else 0,
        "train_token_avg": round(sum(train_tokens) / max(n_train, 1), 1),
    }

    plan_path = out_dir / "dry_run_plan.json"
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    logger.info("Dry-run plan saved to %s", plan_path)
    return report


def train_qlora(config: QLoRAConfig) -> dict[str, Any]:
    """Train or verify QLoRA fine-tuning for Qwen2.5-Coder-7B-Instruct."""
    # Check if real GPU execution requested
    if not config.dry_run:
        try:
            import torch  # type: ignore

            if not torch.cuda.is_available():
                logger.warning("CUDA is not available on this system. Falling back to --dry-run validation.")
                return run_dry_run_verification(config)

            from trl import SFTTrainer  # type: ignore
            from unsloth import FastLanguageModel  # type: ignore
            from transformers import TrainingArguments  # type: ignore

            logger.info("CUDA detected (%s). Initializing FastLanguageModel in 4-bit...", torch.cuda.get_device_name(0))

            model, tokenizer = FastLanguageModel.from_pretrained(
                model_name=config.model_name,
                max_seq_length=config.max_seq_length,
                dtype=None,
                load_in_4bit=config.load_in_4bit,
            )

            model = FastLanguageModel.get_peft_model(
                model,
                r=config.lora_r,
                target_modules=config.target_modules,
                lora_alpha=config.lora_alpha,
                lora_dropout=config.lora_dropout,
                bias=config.bias,
                use_gradient_checkpointing=config.use_gradient_checkpointing,
                random_state=config.seed,
            )

            from datasets import load_dataset  # type: ignore

            dataset = load_dataset(
                "json",
                data_files={"train": config.train_file, "val": config.val_file},
            )

            training_args = TrainingArguments(
                per_device_train_batch_size=config.per_device_train_batch_size,
                gradient_accumulation_steps=config.gradient_accumulation_steps,
                warmup_ratio=config.warmup_ratio,
                num_train_epochs=config.num_train_epochs,
                learning_rate=config.learning_rate,
                fp16=not torch.cuda.is_bf16_supported(),
                bf16=torch.cuda.is_bf16_supported(),
                logging_steps=10,
                optim=config.optim,
                weight_decay=config.weight_decay,
                lr_scheduler_type=config.lr_scheduler_type,
                seed=config.seed,
                output_dir=config.output_dir,
            )

            trainer = SFTTrainer(
                model=model,
                tokenizer=tokenizer,
                train_dataset=dataset["train"],
                eval_dataset=dataset["val"],
                dataset_text_field="messages",
                max_seq_length=config.max_seq_length,
                dataset_num_proc=2,
                packing=False,
                args=training_args,
            )

            logger.info("Starting Unsloth QLoRA training...")
            trainer.train()

            out_dir = Path(config.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(str(out_dir))
            tokenizer.save_pretrained(str(out_dir))

            logger.info("Training complete. LoRA adapter saved to %s", out_dir)
            return {"status": "SUCCESS", "output_dir": str(out_dir)}

        except ImportError as exc:
            logger.warning("Unsloth / PyTorch CUDA dependencies not found (%s). Falling back to --dry-run validation.", exc)
            return run_dry_run_verification(config)

    return run_dry_run_verification(config)


def main() -> None:
    parser = argparse.ArgumentParser(description="Unsloth QLoRA Training for Qwen2.5-Coder-7B-Instruct")
    parser.add_argument("--model-name", type=str, default="Qwen/Qwen2.5-Coder-7B-Instruct")
    parser.add_argument("--train-file", type=str, default="data/train.jsonl")
    parser.add_argument("--val-file", type=str, default="data/val.jsonl")
    parser.add_argument("--output-dir", type=str, default="outputs/qwen2.5-coder-7b-erp-qlora")
    parser.add_argument("--num-epochs", type=int, default=3)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--lora-r", type=int, default=32)
    parser.add_argument("--lora-alpha", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=4)
    parser.add_argument("--max-seq-length", type=int, default=4096)
    parser.add_argument("--dry-run", action="store_true", default=False, help="Run cross-platform dry-run validation")

    args = parser.parse_args()

    cfg = QLoRAConfig(
        model_name=args.model_name,
        train_file=args.train_file,
        val_file=args.val_file,
        output_dir=args.output_dir,
        num_train_epochs=args.num_epochs,
        learning_rate=args.learning_rate,
        lora_r=args.lora_r,
        lora_alpha=args.lora_alpha,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        max_seq_length=args.max_seq_length,
        dry_run=args.dry_run,
    )

    try:
        report = train_qlora(cfg)
        print("\nQLoRA Verification Completed Successfully:")
        print(f"  - Model:           {report['model_name']}")
        print(f"  - Quantization:    4-bit {report['quant_type']}")
        print(f"  - LoRA Config:     r={report['lora_r']}, alpha={report['lora_alpha']}")
        print(f"  - Total Samples:   {report['total_training_samples']} train, {report['total_validation_samples']} val")
        print(f"  - Max Token Len:   {report['train_token_max']} (limit={report['max_seq_length']})")
        print(f"  - Effective Steps: {report['total_training_steps']} across {report['num_train_epochs']} epochs")
        sys.exit(0)
    except Exception as exc:
        print(f"Training failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
