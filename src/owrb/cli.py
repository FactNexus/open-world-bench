from __future__ import annotations

import json
from pathlib import Path

import typer

from owrb.domain_loader import load_domain_pack
from owrb.generation import GenerationError, generate_batch, write_instances
from owrb.providers.builtin import BuiltinProviderFactory
from owrb.schema_export import check_schemas, write_schemas
from owrb.validation import (
    ValidationIssue,
    validate_domain_pack,
    validate_system_definition,
)

app = typer.Typer(help="Open-World Research Benchmark")
domain_app = typer.Typer(help="Validate and inspect domain packs")
scenario_app = typer.Typer(help="Generate and inspect scenario instances")
systems_app = typer.Typer(help="Validate system definitions")
schemas_app = typer.Typer(help="Generate public JSON Schemas from the Pydantic contracts")
app.add_typer(domain_app, name="domain")
app.add_typer(scenario_app, name="scenarios")
app.add_typer(systems_app, name="systems")
app.add_typer(schemas_app, name="schemas")

DEFAULT_DOMAINS_DIRECTORY = Path("domains")
DEFAULT_SCENARIOS_DIRECTORY = Path("runs") / "scenarios"


def _emit_issues(issues: list[ValidationIssue]) -> None:
    for issue in issues:
        typer.echo(f"{issue.severity}: {issue.location}: {issue.message}", err=True)


def _resolve_domain_directory(domain: str) -> Path:
    candidate = Path(domain)
    if candidate.is_dir():
        return candidate
    fallback = DEFAULT_DOMAINS_DIRECTORY / domain
    if fallback.is_dir():
        return fallback
    typer.echo(f"Domain not found: {domain}", err=True)
    raise typer.Exit(code=1)


@domain_app.command("validate")
def validate_domain(
    domain_directory: Path,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Validate a domain pack: manifest, templates, providers, rules, and prompts."""
    result = validate_domain_pack(domain_directory)
    if json_output:
        payload = {
            "valid": result.valid,
            "id": result.domain_pack.id if result.domain_pack else None,
            "version": result.domain_pack.version if result.domain_pack else None,
            "templates": sorted(template.id for template in result.templates),
            "issues": [issue.as_dict() for issue in result.issues],
        }
        typer.echo(json.dumps(payload, indent=2))
    else:
        _emit_issues(result.issues)
        if result.valid and result.domain_pack is not None:
            template_ids = ", ".join(sorted(template.id for template in result.templates))
            typer.echo(f"Valid: {result.domain_pack.id} (templates: {template_ids})")
    if not result.valid:
        raise typer.Exit(code=1)


@domain_app.command("list")
def list_domains(
    directory: Path = typer.Option(
        DEFAULT_DOMAINS_DIRECTORY, "--directory", help="Directory containing domain packs"
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """List domain packs found under a directory."""
    entries = []
    if directory.is_dir():
        for manifest_path in sorted(directory.glob("*/domain.yaml")):
            try:
                domain_pack = load_domain_pack(manifest_path.parent)
            except Exception as error:  # noqa: BLE001 - listing must not abort on one bad pack
                entries.append(
                    {"path": str(manifest_path.parent), "valid": False, "error": str(error)}
                )
                continue
            entries.append(
                {
                    "path": str(manifest_path.parent),
                    "valid": True,
                    "id": domain_pack.id,
                    "name": domain_pack.name,
                    "version": domain_pack.version,
                }
            )
    if json_output:
        typer.echo(json.dumps({"domains": entries}, indent=2))
        return
    if not entries:
        typer.echo(f"No domain packs found under {directory}")
        return
    for entry in entries:
        if entry["valid"]:
            typer.echo(f"{entry['id']}  {entry['version']}  {entry['path']}")
        else:
            typer.echo(f"INVALID  {entry['path']}  ({entry['error']})")


@scenario_app.command("generate")
def generate_scenarios(
    domain: str = typer.Option(..., "--domain", help="Domain pack ID or directory"),
    count: int = typer.Option(..., "--count", min=1, help="Number of instances to generate"),
    seed: int = typer.Option(..., "--seed", help="Suite seed for deterministic generation"),
    output: Path | None = typer.Option(
        None, "--output", help="Output directory (default: runs/scenarios/<domain>-seed<seed>)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Generate and report without writing instance files"
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Generate frozen, reproducible scenario instances from a domain pack."""
    domain_directory = _resolve_domain_directory(domain)
    validation = validate_domain_pack(domain_directory)
    if not validation.valid or validation.domain_pack is None:
        _emit_issues(validation.issues)
        typer.echo("Domain pack is invalid; aborting generation.", err=True)
        raise typer.Exit(code=1)

    factory = BuiltinProviderFactory(domain_directory)
    try:
        instances, report = generate_batch(
            domain_pack=validation.domain_pack,
            templates=validation.templates,
            provider_factory=factory,
            suite_seed=seed,
            count=count,
        )
    except GenerationError as error:
        typer.echo(f"Generation failed: {error}", err=True)
        raise typer.Exit(code=1) from error

    output_directory = output or DEFAULT_SCENARIOS_DIRECTORY / (
        f"{validation.domain_pack.id}-seed{seed}"
    )
    written: list[Path] = []
    if not dry_run:
        written = write_instances(instances, output_directory)
        report_path = output_directory / "generation-report.json"
        report_path.write_text(json.dumps(report.as_dict(), indent=2) + "\n", encoding="utf-8")

    if json_output:
        payload = {
            "output_directory": None if dry_run else str(output_directory),
            "instances": [instance.id for instance in instances],
            "report": report.as_dict(),
        }
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(
            f"Generated {report.generated}/{report.requested} instances "
            f"({report.rule_rejections} rule rejections, "
            f"{report.duplicate_rejections} duplicate rejections)"
        )
        if written:
            typer.echo(f"Wrote {len(written)} instance files to {output_directory}")


@scenario_app.command("inspect")
def inspect_scenario(
    instance_id: str,
    directory: Path = typer.Option(
        DEFAULT_SCENARIOS_DIRECTORY, "--directory", help="Directory to search for instances"
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit the raw instance JSON"),
) -> None:
    """Show a generated scenario instance by ID."""
    matches = sorted(directory.rglob(f"{instance_id}.json")) if directory.is_dir() else []
    if not matches:
        typer.echo(f"Scenario instance not found under {directory}: {instance_id}", err=True)
        raise typer.Exit(code=1)
    content = matches[0].read_text(encoding="utf-8")
    if json_output:
        typer.echo(content.rstrip("\n"))
        return
    instance = json.loads(content)
    typer.echo(f"id:        {instance['id']}")
    typer.echo(f"template:  {instance['template_id']} v{instance['template_version']}")
    typer.echo(f"domain:    {instance['domain_id']} v{instance['domain_version']}")
    typer.echo(f"seed:      {instance['seed']}")
    typer.echo(f"tags:      {', '.join(instance.get('tags', []))}")
    typer.echo("prompt:")
    for line in instance["prompt"].splitlines():
        typer.echo(f"  {line}")


@systems_app.command("validate")
def validate_system(
    system_path: Path,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Validate a system definition YAML file."""
    result = validate_system_definition(system_path)
    if json_output:
        payload = {
            "valid": result.valid,
            "id": result.system.id if result.system else None,
            "adapter": result.system.adapter if result.system else None,
            "issues": [issue.as_dict() for issue in result.issues],
        }
        typer.echo(json.dumps(payload, indent=2))
    else:
        _emit_issues(result.issues)
        if result.valid and result.system is not None:
            typer.echo(f"Valid: {result.system.id} (adapter: {result.system.adapter})")
    if not result.valid:
        raise typer.Exit(code=1)


@schemas_app.command("generate")
def generate_schemas(
    output: Path = typer.Option(Path("schemas"), "--output", help="Schema output directory"),
    check: bool = typer.Option(
        False, "--check", help="Fail if checked-in schemas differ from the models"
    ),
) -> None:
    """Regenerate (or verify) the public JSON Schemas."""
    if check:
        stale = check_schemas(output)
        if stale:
            typer.echo(f"Stale schemas: {', '.join(stale)}", err=True)
            typer.echo("Run 'owrb schemas generate' and commit the result.", err=True)
            raise typer.Exit(code=1)
        typer.echo("Schemas are up to date.")
        return
    written = write_schemas(output)
    typer.echo(f"Wrote {len(written)} schema files to {output}")


@app.command("run")
def run_suite() -> None:
    """Run a benchmark suite. Implementation target for Milestone 2."""
    typer.echo("Suite execution is specified but not implemented yet.", err=True)
    raise typer.Exit(code=2)


@app.command("evaluate")
def evaluate_run_set() -> None:
    """Evaluate a run set. Implementation target for Milestone 3."""
    typer.echo("Evaluation is specified but not implemented yet.", err=True)
    raise typer.Exit(code=2)


@app.command("compare")
def compare_run_set() -> None:
    """Compare systems. Implementation target for Milestone 4."""
    typer.echo("Comparison is specified but not implemented yet.", err=True)
    raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
