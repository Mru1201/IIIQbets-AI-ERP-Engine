"""Tests for Unsloth QLoRA Training Script (Feature 29)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from training.config import QLoRAConfig
from training.train_qlora import estimate_token_count, run_dry_run_verification, train_qlora


def test_qlora_config_hyperparameter_specifications() -> None:
    cfg = QLoRAConfig()

    # Base model
    assert cfg.model_name == "Qwen/Qwen2.5-Coder-7B-Instruct"
    assert cfg.max_seq_length == 4096
    assert cfg.load_in_4bit is True
    assert cfg.quant_type == "nf4"

    # LoRA rank and scaling
    assert cfg.lora_r == 32
    assert cfg.lora_alpha == 64
    assert cfg.lora_dropout == 0.0
    assert set(cfg.target_modules) == {
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
        "gate_proj",
        "up_proj",
        "down_proj",
    }

    # Training schedule
    assert cfg.learning_rate == 2e-4
    assert cfg.lr_scheduler_type == "cosine"
    assert cfg.warmup_ratio == 0.05
    assert cfg.num_train_epochs == 3
    assert cfg.per_device_train_batch_size == 2
    assert cfg.gradient_accumulation_steps == 4
    assert cfg.optim == "paged_adamw_8bit"


def test_token_counter_estimate() -> None:
    text = "SELECT id, name FROM accounts WHERE unpaid_amount > 5000"
    count = estimate_token_count(text)
    assert 5 < count < 50


def test_dry_run_verification_on_real_data(tmp_path: Path) -> None:
    # Use existing generated data if present, or create minimal fixture
    data_dir = Path("erp-ai-model-pipeline/data")
    train_file = data_dir / "train.jsonl"
    val_file = data_dir / "val.jsonl"

    if not train_file.exists():
        train_file = tmp_path / "train.jsonl"
        val_file = tmp_path / "val.jsonl"
        dummy = json.dumps({
            "messages": [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "user query"},
                {"role": "assistant", "content": json.dumps({
                    "thought_process": "ok",
                    "tables_used": ["voucher"],
                    "metrics_applied": [],
                    "sql_query": "SELECT 1",
                    "suggested_visualization": "table",
                    "error": None,
                })},
            ]
        }) + "\n"
        train_file.write_text(dummy * 10)
        val_file.write_text(dummy * 2)

    cfg = QLoRAConfig(
        train_file=str(train_file),
        val_file=str(val_file),
        output_dir=str(tmp_path / "outputs"),
        dry_run=True,
    )

    report = train_qlora(cfg)
    assert report["status"] == "DRY_RUN_PASSED"
    assert report["effective_batch_size"] == 8
    assert report["train_token_max"] <= 4096
    assert (tmp_path / "outputs" / "dry_run_plan.json").exists()
