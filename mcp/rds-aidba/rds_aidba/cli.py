"""
rds-aidba CLI — Interactive DBA console.
Author: Kiran Mayee Mulupuru, Sr. Specialist Database TAM, AWS Premium Support

✅ UPDATED:
- Displays detected environment (Aurora/RDS/EC2) in the session header
  after the silent probe — never dumps raw probe results to the user
- /health and category checks pass through cleanly
- CloudWatch log analysis commands unchanged
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

import click
import yaml

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich import print as rprint
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from rds_aidba.agent import AIDBAAgent
from rds_aidba.mcp_client import ClusterConfig, MySQLMCPClient
from rds_aidba.health_checks import CATEGORIES

console = Console() if RICH_AVAILABLE else None

BANNER = """
╔══════════════════════════════════════════════════════════════════════╗
║              rds-aidba · AI-Powered DBA for AWS                      ║
║         Amazon Aurora & RDS (MySQL + PostgreSQL) Platform            ║
╠══════════════════════════════════════════════════════════════════════╣
║  Author : Kiran Mayee Mulupuru                                       ║
║  Role   : Sr. Specialist Database TAM, AWS Enterprise Support        ║
╠══════════════════════════════════════════════════════════════════════╣
║  Type your question in plain English, or use a command below:        ║
║    /health            — Full health check                            ║
║    /health <cat>      — Category check (connections, innodb, …)      ║
║    /cloudwatch        — Analyze CloudWatch logs (last 1 hour)        ║
║    /cloudwatch        — Analyze CloudWatch logs (custom hours)       ║
║    /categories        — List available check categories              ║
║    /reset             — Clear conversation history                   ║
║    /help              — Show this help                               ║
║    /quit or exit      — Quit                                         ║
╚══════════════════════════════════════════════════════════════════════╝
"""


def _load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        click.echo(f"[ERROR] Config file not found: {config_path}", err=True)
        sys.exit(1)
    with open(path) as f:
        return yaml.safe_load(f)


def _build_cluster_config(
    cfg: dict,
    cluster_name: str,
    profile_override: Optional[str],
    region_override: Optional[str],
) -> ClusterConfig:
    clusters = cfg.get("clusters", {})
    if cluster_name not in clusters:
        click.echo(
            f"[ERROR] Cluster '{cluster_name}' not found. "
            f"Available: {list(clusters.keys())}",
            err=True,
        )
        sys.exit(1)

    c       = clusters[cluster_name]
    aws_cfg = cfg.get("aws", {})
    region  = region_override or aws_cfg.get("region", "us-east-1")
    profile = profile_override or aws_cfg.get("profile", "default")

    return ClusterConfig(
        name=cluster_name,
        secret_arn=c["secret_arn"],
        database=c["database"],
        region=region,
        readonly=c.get("readonly", True),
        resource_arn=c.get("resource_arn"),
        hostname=c.get("hostname"),
        port=c.get("port", 3306),
        aws_profile=profile,
        cloudwatch_log_group=c.get("cloudwatch_log_group"),
        cloudwatch_slow_query_log_group=c.get("cloudwatch_slow_query_log_group"),
        cloudwatch_audit_log_group=c.get("cloudwatch_audit_log_group"),
    )


def _print(text: str, style: str = "") -> None:
    if RICH_AVAILABLE and console:
        if style:
            console.print(text, style=style)
        else:
            console.print(Markdown(text))
    else:
        print(text)


def _print_banner() -> None:
    if RICH_AVAILABLE and console:
        console.print(Panel(BANNER, style="bold cyan", expand=False))
    else:
        print(BANNER)


def _print_response(response: str) -> None:
    if RICH_AVAILABLE and console:
        console.print(
            Panel(
                Markdown(response),
                title="[bold green]AI-DBA Analysis[/]",
                border_style="green",
            )
        )
    else:
        print("\n--- AI-DBA Analysis ---")
        print(response)
        print("-----------------------\n")


async def _probe_and_show_env(cluster_cfg: ClusterConfig) -> str:
    """
    Run the silent environment probe and return a one-line status string
    for display in the session header.
    Does NOT print raw SQL results — only the human-readable description.
    """
    try:
        async with MySQLMCPClient(cluster_cfg) as mcp:
            env = await mcp.probe_environment()
        return env.describe()
    except Exception as exc:
        logger = logging.getLogger(__name__)
        logger.debug("Environment probe failed: %s", exc)
        return "Environment detection pending (will detect on first query)"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

@click.command()
@click.option("--cluster",  "-c", default="primary",
              help="Cluster name from config.yaml (default: primary)")
@click.option("--config",   "-f", default="config/config.yaml",
              help="Path to config.yaml (default: config/config.yaml)")
@click.option("--profile",  "-p", default=None,
              help="AWS CLI profile override")
@click.option("--region",   "-r", default=None,
              help="AWS region override")
@click.option("--model",    "-m", default=None,
              help="Bedrock model ID override")
@click.option("--health",   is_flag=True,
              help="Run full health check non-interactively and exit")
@click.option("--category", default=None,
              help="Run a single category check non-interactively and exit")
@click.option("--cloudwatch-logs", is_flag=True,
              help="Analyze CloudWatch logs non-interactively and exit")
@click.option("--hours-back", default=1, type=int,
              help="Hours to look back for CloudWatch logs (default: 1, max: 24)")
@click.option("--verbose",  "-v", is_flag=True,
              help="Enable debug logging")
def main(
    cluster: str,
    config: str,
    profile: Optional[str],
    region: Optional[str],
    model: Optional[str],
    health: bool,
    category: Optional[str],
    cloudwatch_logs: bool,
    hours_back: int,
    verbose: bool,
) -> None:
    """
    rds-aidba — AI-Powered DBA for Amazon Aurora & RDS (MySQL + PostgreSQL).

    Ask questions in plain English and get AI-driven DBA insights powered
    by Amazon Bedrock and the AWS Labs MySQL MCP Server.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    cfg         = _load_config(config)
    cluster_cfg = _build_cluster_config(cfg, cluster, profile, region)

    bedrock_cfg = cfg.get("bedrock", {})
    model_id    = model or bedrock_cfg.get("model_id", AIDBAAgent.DEFAULT_MODEL)
    aws_region  = region  or cfg.get("aws", {}).get("region", "us-east-1")
    aws_profile = profile or cfg.get("aws", {}).get("profile", "default")

    agent = AIDBAAgent(
        cluster_config=cluster_cfg,
        bedrock_model_id=model_id,
        aws_region=aws_region,
        aws_profile=aws_profile,
    )

    # ------------------------------------------------------------------
    # Non-interactive modes
    # ------------------------------------------------------------------
    if health:
        _print(f"Running full health check on cluster '{cluster}'...", style="yellow")
        result = asyncio.run(agent.run_full_health_check())
        _print_response(result)
        return

    if category:
        _print(f"Running '{category}' check on cluster '{cluster}'...", style="yellow")
        result = asyncio.run(agent.run_category_check(category))
        _print_response(result)
        return

    if cloudwatch_logs:
        if hours_back < 1 or hours_back > 24:
            _print("[ERROR] --hours-back must be between 1 and 24", style="bold red")
            sys.exit(1)
        if not cluster_cfg.cloudwatch_log_group:
            _print(
                "[ERROR] CloudWatch log groups not configured in config.yaml",
                style="bold red",
            )
            sys.exit(1)
        _print(
            f"Analyzing CloudWatch logs for '{cluster}' (last {hours_back}h)...",
            style="yellow",
        )
        result = asyncio.run(agent.analyze_cloudwatch_logs(hours_back=hours_back))
        _print_response(result)
        return

    # ------------------------------------------------------------------
    # Interactive REPL
    # ------------------------------------------------------------------
    _print_banner()

    # ✅ Silent environment probe — shown as a clean one-liner in the header,
    # NOT as raw SQL output dumped to the terminal
    _print("Detecting cluster environment...", style="dim")
    env_description = asyncio.run(_probe_and_show_env(cluster_cfg))

    _print(f"🤖 Cluster  : **{cluster}**  |  Model : **{model_id}**\n")
    _print(f"🌍 Region   : **{aws_region}**  |  Profile : **{aws_profile}**\n")
    _print(f"🔍 Environment: **{env_description}**\n")

    while True:
        try:
            if RICH_AVAILABLE:
                user_input = Prompt.ask("[bold cyan]You[/]").strip()
            else:
                user_input = input("You> ").strip()
        except (KeyboardInterrupt, EOFError):
            _print("\nGoodbye!", style="bold yellow")
            break

        if not user_input:
            continue

        # Built-in commands
        if user_input.lower() in ("/quit", "exit", "quit"):
            _print("Goodbye!", style="bold yellow")
            break

        if user_input.lower() == "/help":
            _print_banner()
            continue

        if user_input.lower() == "/reset":
            agent.reset_conversation()
            _print("Conversation history cleared.", style="green")
            continue

        if user_input.lower() == "/categories":
            cats = "\n".join(f"  - **{k}**" for k in CATEGORIES.keys())
            _print(f"Available categories:\n{cats}")
            continue

        if user_input.lower() == "/health":
            _print(f"Running full health check on '{cluster}'...", style="yellow")
            result = asyncio.run(agent.run_full_health_check())
            _print_response(result)
            continue

        if user_input.lower().startswith("/health "):
            cat = user_input.split(None, 1)[1].strip()
            _print(f"Running '{cat}' check...", style="yellow")
            result = asyncio.run(agent.run_category_check(cat))
            _print_response(result)
            continue

        if user_input.lower() == "/cloudwatch":
            _print("Analyzing CloudWatch logs (last 1 hour)...", style="yellow")
            result = asyncio.run(agent.analyze_cloudwatch_logs(hours_back=1))
            _print_response(result)
            continue

        if user_input.lower().startswith("/cloudwatch "):
            try:
                hours = int(user_input.split(None, 1)[1].strip())
                if hours < 1 or hours > 24:
                    _print("[ERROR] Hours must be between 1 and 24", style="bold red")
                    continue
                _print(
                    f"Analyzing CloudWatch logs (last {hours}h)...", style="yellow"
                )
                result = asyncio.run(agent.analyze_cloudwatch_logs(hours_back=hours))
                _print_response(result)
            except ValueError:
                _print(
                    "[ERROR] Invalid hours. Usage: /cloudwatch ",
                    style="bold red",
                )
            continue

        # Plain-English question → agent
        _print("Analysing...", style="dim")
        try:
            response = asyncio.run(agent.chat(user_input))
            _print_response(response)
        except Exception as exc:
            _print(f"[ERROR] {exc}", style="bold red")
            if verbose:
                raise


if __name__ == "__main__":
    main()