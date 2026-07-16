from __future__ import annotations

import json
from pathlib import Path

import typer
from pydantic import ValidationError

from owrb.domain_loader import load_domain_pack

app = typer.Typer(help="Open-World Research Benchmark")
domain_app = typer.Typer(help="Validate and inspect domain packs")
scenario_app = typer.Typer(help="Generate and inspect scenario instances")
app.add_typer(domain_app, name="domain")
app.add_typer(scenario_app, name="scenarios")


@domain_app.command("validate")
def validate_domain(
    domain_directory: Path,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Validate a domain manifest. Template/provider validation is a Milestone 0 target."""
    try:
        domain_pack = load_domain_pack(domain_directory)
    except (FileNotFoundError, ValidationError, ValueError) as error:
        if json_output:
            typer.echo(json.dumps({"valid": False, "error": str(error)}, indent=2))
        else:
            typer.echo(f"Invalid domain pack: {error}", err=True)
        raise typer.Exit(code=1) from error

    result = {
        "valid": True,
        "id": domain_pack.id,
        "version": domain_pack.version,
        "templates": domain_pack.templates,
    }
    typer.echo(json.dumps(result, indent=2) if json_output else f"Valid: {domain_pack.id}")


@scenario_app.command("generate")
def generate_scenarios() -> None:
    """Generate frozen scenarios. Implementation target for Milestone 1."""
    typer.echo("Scenario generation is specified but not implemented in this scaffold.", err=True)
    raise typer.Exit(code=2)


@app.command("run")
def run_suite() -> None:
    """Run a benchmark suite. Implementation target for Milestone 2."""
    typer.echo("Suite execution is specified but not implemented in this scaffold.", err=True)
    raise typer.Exit(code=2)


@app.command("evaluate")
def evaluate_run_set() -> None:
    """Evaluate a run set. Implementation target for Milestone 3."""
    typer.echo("Evaluation is specified but not implemented in this scaffold.", err=True)
    raise typer.Exit(code=2)


@app.command("compare")
def compare_run_set() -> None:
    """Compare systems. Implementation target for Milestone 4."""
    typer.echo("Comparison is specified but not implemented in this scaffold.", err=True)
    raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
