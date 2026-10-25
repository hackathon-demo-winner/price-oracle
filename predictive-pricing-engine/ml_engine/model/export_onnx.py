"""Export the five product boosters to ONNX so they can run in Node (onnxruntime-node).

    python -m ml_engine.model.export_onnx

Writes weights/product_30d/onnx/{p5,p25,p50,p75,p95}.onnx and onnx_export.json (input name,
feature order, conformal offsets, parity check). Each graph maps float32[N, n_features] to the
raw log-ratio quantile; the caller must then do what QuantileModel.predict_log_ratio does:
sort the five values, apply the conformal offsets, sort again, and multiply exp() by the price.
Missing features are passed as NaN.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..config import QUANTILE_NAMES
from .quantile_regressor import PRODUCT_MODEL_DIR, QuantileModel


def export(directory: Path = PRODUCT_MODEL_DIR, parity_rows: int = 5000, seed: int = 3) -> dict:
    import onnxruntime as ort
    from onnxmltools import convert_lightgbm
    from onnxmltools.convert.common.data_types import FloatTensorType

    model = QuantileModel.load(directory)
    n_features = len(model.features)
    out_dir = Path(directory) / "onnx"
    out_dir.mkdir(exist_ok=True)

    # Parity sample: real feature rows when the training cache is present, else random with NaNs.
    from .train import load_synthetic_dataset
    frame = load_synthetic_dataset()
    X = frame[model.features].sample(n=min(parity_rows, len(frame)), random_state=seed).to_numpy(np.float64)
    X32 = X.astype(np.float32)

    report = {"input_name": "input", "input_dtype": "float32", "features": model.features,
              "conformal_offsets": model.offsets, "files": {}, "parity": {}}
    for name, booster in zip(QUANTILE_NAMES, model.boosters):
        onnx_model = convert_lightgbm(booster, initial_types=[("input", FloatTensorType([None, n_features]))], target_opset=15)
        path = out_dir / f"{name}.onnx"
        path.write_bytes(onnx_model.SerializeToString())
        session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        onnx_pred = session.run(None, {"input": X32})[0].reshape(-1)
        # Compare against LightGBM fed the same float32-rounded inputs, so only the runtime differs.
        lgb_pred = booster.predict(X32.astype(np.float64))
        diff = np.abs(onnx_pred - lgb_pred)
        report["files"][name] = path.name
        report["parity"][name] = {"rows": int(len(diff)), "max_abs_diff": float(diff.max()),
                                  "rows_over_1e-4": int((diff > 1e-4).sum())}
    (out_dir / "onnx_export.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


if __name__ == "__main__":
    result = export()
    for name, parity in result["parity"].items():
        print(name, parity)
