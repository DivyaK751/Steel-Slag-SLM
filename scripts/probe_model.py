"""Ask a model the terminology probe questions and save its answers for review.

Works with a local Ollama model or a vLLM server (directly or through an SSH tunnel).

Usage:
    uv run python scripts/probe_model.py --backend ollama --model qwen3:8b --thinking on
    uv run python scripts/probe_model.py --backend vllm --model qwen3-8b --thinking on
"""

import argparse
import json
import time
from pathlib import Path

import requests
import yaml

DEFAULT_URLS = {"ollama": "http://localhost:11434", "vllm": "http://localhost:8000/v1"}
SYSTEM_PROMPT = "You are a materials science assistant. Answer in 2-4 sentences."
# Qwen3's recommended sampling; greedy decoding makes thinking mode loop.
SAMPLING = {
    "on": {"temperature": 0.6, "top_p": 0.95, "top_k": 20},
    "off": {"temperature": 0.7, "top_p": 0.8, "top_k": 20},
}
MAX_TOKENS = 8192


def load_questions(path: Path) -> list[dict]:
    """Load the probe questions from a YAML file."""
    return yaml.safe_load(path.read_text())["questions"]


def build_messages(question: str) -> list[dict]:
    """Return the chat messages for one question."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]


def ask_ollama(base_url: str, model: str, question: str, thinking: str) -> tuple[str, str]:
    """Ask an Ollama model; return (answer, thinking text).

    Args:
        thinking: "on" or "off" sets Qwen3's thinking mode; "default" leaves it to the model,
            which is needed for models without a thinking mode.
    """
    payload: dict = {
        "model": model,
        "messages": build_messages(question),
        "stream": False,
        "options": {**SAMPLING.get(thinking, SAMPLING["off"]), "num_predict": MAX_TOKENS},
    }
    if thinking != "default":
        payload["think"] = thinking == "on"
    response = requests.post(f"{base_url}/api/chat", json=payload, timeout=900)
    response.raise_for_status()
    message = response.json()["message"]
    return message["content"].strip(), message.get("thinking", "")


def ask_vllm(base_url: str, model: str, question: str, thinking: str) -> tuple[str, str]:
    """Ask a model on a vLLM server (OpenAI-style API); return (answer, thinking text)."""
    payload: dict = {
        "model": model,
        "messages": build_messages(question),
        "max_tokens": MAX_TOKENS,
        **SAMPLING.get(thinking, SAMPLING["off"]),
    }
    if thinking != "default":
        payload["chat_template_kwargs"] = {"enable_thinking": thinking == "on"}
    response = requests.post(f"{base_url}/chat/completions", json=payload, timeout=900)
    response.raise_for_status()
    message = response.json()["choices"][0]["message"]
    # Newer vLLM versions name the field "reasoning"; older ones "reasoning_content".
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    return (message.get("content") or "").strip(), reasoning


def write_review(records: list[dict], title: str, path: Path) -> None:
    """Write answers next to the reference answers as Markdown for reading."""
    lines = [f"# {title}\n"]
    for record in records:
        lines += [
            f"## {record['id']} ({record['category']})",
            f"**Q:** {record['question']}\n",
            f"**Reference:** {record['reference']}\n",
            f"**Model:** {record['answer']}\n",
        ]
    path.write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", choices=["ollama", "vllm"], default="ollama")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--base-url", default=None, help="Defaults to the backend's local URL.")
    parser.add_argument("--questions", type=Path, default=Path("configs/terminology_probe.yaml"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/interim/model_probe"))
    parser.add_argument("--thinking", choices=["off", "on", "default"], default="off")
    parser.add_argument("--limit", type=int, default=None, help="Ask only the first N questions.")
    args = parser.parse_args()

    ask = ask_vllm if args.backend == "vllm" else ask_ollama
    base_url = args.base_url or DEFAULT_URLS[args.backend]
    questions = load_questions(args.questions)[: args.limit]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    run_name = f"{args.model}_{args.backend}_thinking-{args.thinking}"
    stem = run_name.replace(":", "_").replace("/", "_")

    records = []
    for index, item in enumerate(questions, start=1):
        started = time.monotonic()
        answer, reasoning = ask(base_url, args.model, item["question"], args.thinking)
        seconds = round(time.monotonic() - started, 1)
        records.append(
            {
                **item,
                "model": args.model,
                "backend": args.backend,
                "thinking_mode": args.thinking,
                "answer": answer,
                "reasoning": reasoning,
                "seconds": seconds,
            }
        )
        print(f"[{index}/{len(questions)}] {item['id']} ({seconds}s)")

    jsonl_path = args.out_dir / f"{stem}.jsonl"
    jsonl_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    write_review(records, run_name, args.out_dir / f"{stem}.md")
    print(f"Saved {jsonl_path} and {stem}.md")


if __name__ == "__main__":
    main()
