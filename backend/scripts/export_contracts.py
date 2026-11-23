"""Regenerate /contracts/*.json from the Python models.  Run:  python scripts/export_contracts.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.contracts import Envelope, NodeResult, Workflow, node_catalog  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "contracts"


def render() -> dict[str, str]:
    files = {
        "workflow.schema.json": Workflow.model_json_schema(),
        "envelope.schema.json": Envelope.model_json_schema(),
        "node-result.schema.json": NodeResult.model_json_schema(),
        "node-types.json": node_catalog(),
    }
    return {name: json.dumps(body, indent=2, sort_keys=True) + "\n" for name, body in files.items()}


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, text in render().items():
        (OUT / name).write_text(text)
        print("wrote", OUT / name)
