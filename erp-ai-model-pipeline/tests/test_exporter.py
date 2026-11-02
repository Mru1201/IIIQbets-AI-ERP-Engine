"""Tests for Dual Format Model Exporter (Feature 30)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from training.export import MODELFILE_TEMPLATE, export_dual_formats


def test_export_dual_formats_dry_run(tmp_path: Path) -> None:
    export_dir = tmp_path / "exported"
    results = export_dual_formats(
        output_dir=export_dir,
        formats=["safetensors", "gguf"],
        quantization_method="q4_k_m",
        dry_run=True,
    )

    # 1. Check safetensors
    assert "safetensors_dir" in results
    safetensors_path = Path(results["safetensors_dir"])
    assert safetensors_path.exists()
    assert (safetensors_path / "config.json").exists()
    config_data = json.loads((safetensors_path / "config.json").read_text())
    assert config_data["model_type"] == "qwen2"
    assert "Qwen2ForCausalLM" in config_data["architectures"]
    assert (safetensors_path / "model.safetensors").exists()

    # 2. Check GGUF
    assert "gguf_path" in results
    gguf_file = Path(results["gguf_path"])
    assert gguf_file.exists()
    assert gguf_file.name == "qwen2.5-coder-7b-erp-q4_k_m.gguf"

    # 3. Check Modelfile
    assert "modelfile_path" in results
    modelfile = Path(results["modelfile_path"])
    assert modelfile.exists()
    content = modelfile.read_text(encoding="utf-8")
    assert "FROM ./qwen2.5-coder-7b-erp-q4_k_m.gguf" in content
    assert "<|im_start|>system" in content
    assert "<|im_end|>" in content
    assert "PARAMETER num_ctx 4096" in content
    assert "PARAMETER temperature 0.0" in content


def test_export_single_format(tmp_path: Path) -> None:
    export_dir = tmp_path / "export_single"
    results = export_dual_formats(
        output_dir=export_dir,
        formats=["safetensors"],
        dry_run=True,
    )
    assert "safetensors_dir" in results
    assert "gguf_path" not in results
