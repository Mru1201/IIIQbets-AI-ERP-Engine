"""QLoRA Training Configuration Dataclasses (Feature 29)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class QLoRAConfig:
    """Hyperparameters and configuration for Qwen2.5-Coder-7B-Instruct fine-tuning."""

    # Model & Architecture
    model_name: str = "Qwen/Qwen2.5-Coder-7B-Instruct"
    max_seq_length: int = 4096
    load_in_4bit: bool = True
    quant_type: str = "nf4"

    # LoRA / PEFT Parameters
    lora_r: int = 32
    lora_alpha: int = 64
    lora_dropout: float = 0.0
    target_modules: list[str] = field(
        default_factory=lambda: [
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ]
    )
    bias: str = "none"
    use_gradient_checkpointing: str = "unsloth"

    # Training Hyperparameters
    learning_rate: float = 2e-4
    lr_scheduler_type: str = "cosine"
    warmup_ratio: float = 0.05
    num_train_epochs: int = 3
    per_device_train_batch_size: int = 2
    gradient_accumulation_steps: int = 4
    optim: str = "paged_adamw_8bit"
    weight_decay: float = 0.01
    seed: int = 42

    # Paths & Operational Flags
    train_file: str = "data/train.jsonl"
    val_file: str = "data/val.jsonl"
    output_dir: str = "outputs/qwen2.5-coder-7b-erp-qlora"
    export_dir: str = "exported_models"
    export_formats: str = "safetensors,gguf"
    dry_run: bool = False
