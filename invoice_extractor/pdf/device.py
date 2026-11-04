from __future__ import annotations
import logging
import os

logger = logging.getLogger("invoice_extractor")

# OCR_DEVICE: "auto" (default) uses the GPU when CUDA is usable, else the CPU;
# "cuda"/"gpu" asks for the GPU but still falls back to the CPU if CUDA isn't
# usable; "cpu" always uses the CPU.
_VALID = {"auto", "cuda", "gpu", "cpu"}


def _requested() -> str:
    value = os.environ.get("OCR_DEVICE", "auto").strip().lower()
    if value not in _VALID:
        logger.warning("Unknown OCR_DEVICE=%r (expected auto, cuda or cpu) -- using auto", value)
        return "auto"
    return "cuda" if value == "gpu" else value


def _torch_cuda_name() -> str | None:
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
    except Exception:
        pass
    return None


def _onnx_cuda_available() -> bool:
    try:
        import onnxruntime
        return "CUDAExecutionProvider" in onnxruntime.get_available_providers()
    except Exception:
        return False


def _announce(engine: str, requested: str, use_cuda: bool, detail: str, missing: str) -> None:
    if use_cuda:
        print(f"[pipeline] {engine} running on GPU (CUDA{': ' + detail if detail else ''})")
        return
    if requested == "cuda":
        logger.warning("%s: OCR_DEVICE=cuda but %s -- falling back to CPU", engine, missing)
        print(f"[pipeline] {engine} running on CPU (CUDA requested but {missing})")
    elif requested == "cpu":
        print(f"[pipeline] {engine} running on CPU (OCR_DEVICE=cpu)")
    else:
        print(f"[pipeline] {engine} running on CPU (no CUDA available)")


def docling_device() -> str:
    """'cuda' or 'cpu' for Docling, which runs its models through torch."""
    requested = _requested()
    gpu_name = _torch_cuda_name() if requested != "cpu" else None
    use_cuda = gpu_name is not None
    _announce("Docling", requested, use_cuda, gpu_name or "", "torch has no CUDA support or no GPU was found")
    return "cuda" if use_cuda else "cpu"


def rapidocr_use_cuda() -> bool:
    """Whether RapidOCR should use onnxruntime's CUDA provider."""
    requested = _requested()
    use_cuda = requested != "cpu" and _onnx_cuda_available()
    _announce("RapidOCR", requested, use_cuda, "", "onnxruntime has no CUDAExecutionProvider (install onnxruntime-gpu)")
    return use_cuda
