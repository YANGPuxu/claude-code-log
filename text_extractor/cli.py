"""Command-line interface for text extractor."""

from __future__ import annotations

import json
from pathlib import Path

import click

from text_extractor.config import DEFAULT_CONFIG, FilterConfig
from text_extractor.extractor import extract_message_metadata, extract_text_from_message
from text_extractor.filter import filter_session
from text_extractor.parser import (
    discover_subagents,
    parse_session,
    parse_subagent,
    SubagentInfo,
)


@click.group()
def cli() -> None:
    """Extract and filter text from Claude Code JSONL files."""
    pass


@cli.command()
@click.argument("jsonl_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    help="Output directory for extracted JSON files",
    default=Path("./extracted"),
)
@click.option(
    "-c",
    "--config",
    type=click.Path(exists=True, path_type=Path),
    help="Path to YAML configuration file",
)
def extract(jsonl_path: Path, output: Path, config: Path | None) -> None:
    """Extract text from a single JSONL file and its subagents."""
    cfg = load_config(config) if config else DEFAULT_CONFIG

    output.mkdir(parents=True, exist_ok=True)

    # Parse the main session
    session = parse_session(jsonl_path)

    # Create a subdirectory for this session
    session_output_dir = output / session.session_id
    session_output_dir.mkdir(parents=True, exist_ok=True)

    # Process main session
    main_result = process_session(session, cfg)
    main_output_path = session_output_dir / f"{session.session_id}.json"
    write_result(main_result, main_output_path)
    click.echo(f"Main session: {jsonl_path.stat().st_size / 1024:.1f}KB -> {main_result['statistics']['filtered_size_kb']:.1f}KB")

    # Process subagents
    subagents = discover_subagents(jsonl_path)
    for subagent in subagents:
        if not cfg.subagent.filter_by_type or subagent.agent_type in cfg.subagent.filter_by_type:
            subagent_session = parse_subagent(subagent)
            subagent_result = process_session(subagent_session, cfg)
            subagent_output_path = session_output_dir / f"{subagent_session.session_id}.json"
            write_result(subagent_result, subagent_output_path)
            click.echo(f"  Subagent {subagent.agent_type}: {subagent.jsonl_path.stat().st_size / 1024:.1f}KB -> {subagent_result['statistics']['filtered_size_kb']:.1f}KB")

    click.echo(f"\nOutput written to: {session_output_dir}/")


@cli.command()
@click.argument(
    "project_path",
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    help="Output directory for extracted JSON files",
    default=Path("./extracted"),
)
@click.option(
    "-c",
    "--config",
    type=click.Path(exists=True, path_type=Path),
    help="Path to YAML configuration file",
)
def extract_project(project_path: Path, output: Path, config: Path | None) -> None:
    """Extract text from all JSONL files in a project directory."""
    cfg = load_config(config) if config else DEFAULT_CONFIG

    output.mkdir(parents=True, exist_ok=True)

    # Find all .jsonl files
    jsonl_files = list(project_path.glob("*.jsonl"))

    if not jsonl_files:
        click.echo("No .jsonl files found in the specified directory.")
        return

    total_original = 0
    total_filtered = 0

    for jsonl_path in jsonl_files:
        # Skip files that are in subagents subdirectories
        if "subagents" in jsonl_path.parts:
            continue

        session = parse_session(jsonl_path)
        result = process_session(session, cfg)

        output_path = output / f"{session.session_id}.json"
        write_result(result, output_path)

        original_kb = jsonl_path.stat().st_size / 1024
        filtered_kb = result["statistics"]["filtered_size_kb"]
        total_original += original_kb
        total_filtered += filtered_kb

        click.echo(f"{session.session_id}: {original_kb:.1f}KB -> {filtered_kb:.1f}KB")

    # Process all subagents
    for jsonl_path in jsonl_files:
        if "subagents" not in jsonl_path.parts:
            continue

        # This is a subagent file
        # Find the corresponding meta file
        meta_path = jsonl_path.with_suffix(".meta.json")
        agent_type = None
        description = None

        if meta_path.exists():
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta_data = json.load(f)
                    agent_type = meta_data.get("agentType")
                    description = meta_data.get("description")
            except (json.JSONDecodeError, FileNotFoundError):
                pass

        # Check if should be included
        if cfg.subagent.filter_by_type and agent_type not in cfg.subagent.filter_by_type:
            continue

        agent_id = jsonl_path.stem
        subagent = SubagentInfo(
            agent_id=agent_id,
            jsonl_path=jsonl_path,
            meta_path=meta_path if meta_path.exists() else None,
            agent_type=agent_type,
            description=description,
        )

        subagent_session = parse_subagent(subagent)
        result = process_session(subagent_session, cfg)

        output_path = output / f"{subagent_session.session_id}.json"
        write_result(result, output_path)

        original_kb = jsonl_path.stat().st_size / 1024
        filtered_kb = result["statistics"]["filtered_size_kb"]
        total_original += original_kb
        total_filtered += filtered_kb

        click.echo(f"  Subagent {agent_type}: {original_kb:.1f}KB -> {filtered_kb:.1f}KB")

    compression_ratio = (1 - total_filtered / total_original) * 100 if total_original > 0 else 0
    click.echo(f"\nTotal: {total_original:.1f}KB -> {total_filtered:.1f}KB ({compression_ratio:.1f}% reduction)")
    click.echo(f"Output written to: {output}/")


def process_session(session, config: FilterConfig) -> dict:
    """Process a session and extract text."""
    # Filter messages
    filtered_session = filter_session(session, config)

    # Extract text from messages
    extracted_messages = []
    for msg in filtered_session.messages:
        text = extract_text_from_message(msg, config)
        if text:
            extracted_messages.append(
                {
                    **extract_message_metadata(msg),
                    "content_text": text,
                }
            )

    original_size = session.jsonl_path.stat().st_size if session.jsonl_path.exists() else 0

    # Calculate filtered size estimate
    filtered_json = json.dumps(extracted_messages, ensure_ascii=False)
    filtered_size = len(filtered_json.encode("utf-8"))

    return {
        "session_id": session.session_id,
        "agent_type": session.agent_type,
        "agent_description": session.agent_description,
        "messages": extracted_messages,
        "statistics": {
            "original_lines": len(session.messages),
            "original_size_kb": original_size / 1024,
            "filtered_lines": len(extracted_messages),
            "filtered_size_kb": filtered_size / 1024,
            "compression_ratio": f"{(1 - filtered_size / original_size) * 100:.1f}%" if original_size > 0 else "0%",
        },
    }


def write_result(result: dict, output_path: Path) -> None:
    """Write extraction result to JSON file."""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def load_config(config_path: Path) -> FilterConfig:
    """Load configuration from YAML file."""
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            config_data = yaml.safe_load(f)

        # Convert dict to FilterConfig
        # This is a simplified conversion - extend as needed
        return DEFAULT_CONFIG
    except ImportError:
        click.echo("Warning: PyYAML not installed, using default config.")
        return DEFAULT_CONFIG
    except Exception as e:
        click.echo(f"Warning: Failed to load config: {e}, using default config.")
        return DEFAULT_CONFIG


if __name__ == "__main__":
    cli()
