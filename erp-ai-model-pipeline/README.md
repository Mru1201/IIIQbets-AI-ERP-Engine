# ERP AI Model Pipeline — Phase 2 Fine-Tuning & Evaluation

This directory contains the end-to-end dataset synthesis, QLoRA fine-tuning, dual-format model export, and benchmark evaluation suite for the ERP Ledger Reporting agent.

## Features
- **Feature 28: Synthetic Dataset Builder (`dataset/build_dataset.py`)**:
  Generates 3,000 hybrid ChatML samples (2,100 BIRD-SQL, 600 Gretel ERP, 300 Adversarial Refusals) with Pydantic and SQL AST validation and a 90/10 train/val split.
- **Feature 29: Unsloth QLoRA Training Script (`training/train_qlora.py`)**:
  QLoRA fine-tuner for `Qwen/Qwen2.5-Coder-7B-Instruct` with 4-bit `nf4`, $r=32$, $\alpha=64$, $lr=2e-4$, cosine schedule, and cross-platform `--dry-run` support.
- **Feature 30: Dual Format Model Exporter (`training/export.py`)**:
  Exports fine-tuned models to merged 16-bit safetensors (for GPU vLLM serving) and `Q4_K_M` GGUF (for zero-setup local Ollama serving) with companion `Modelfile`.
- **Feature 32: Gold Evaluation Suite (`benchmarks/gold_eval_suite.py`)**:
  200 curated ERP queries evaluating Syntax Validity, AST Guardrail compliance, and Execution Accuracy (EX%).

## Usage
```bash
# 1. Build 3,000 hybrid samples
python -m dataset.build_dataset --total-samples 3000 --output-dir data

# 2. Dry-run training verification
python -m training.train_qlora --dry-run --train-file data/train.jsonl --val-file data/val.jsonl

# 3. Dual export models
python -m training.export --dry-run --output-dir exported_models --formats safetensors,gguf
```
