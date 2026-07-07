"""
MkDocs hook: generates the custom agents catalog data from README files.

At build time, scans custom-agents/*/README.md, extracts metadata, and writes
docs/javascripts/agents-data.json. The JS on the catalog page reads this
JSON to render cards dynamically.

This also generates individual agent doc stubs if a docs/custom-agents/<name>.md
doesn't already exist, so new agents appear on the site automatically.
"""

import json
import re
from pathlib import Path


def on_pre_build(config, **kwargs):
    """Generate agents-data.json and agent doc stubs before the build."""
    config_dir = config["docs_dir"].replace("/docs", "")
    catalog = _build_agents_catalog(config_dir)

    # Write JSON for the JS to consume (only if changed, to avoid dev-server loop)
    js_dir = Path(config["docs_dir"]) / "javascripts"
    js_dir.mkdir(parents=True, exist_ok=True)
    data_path = js_dir / "agents-data.json"
    new_content = json.dumps(catalog, indent=2)
    if data_path.is_file() and data_path.read_text(encoding="utf-8") == new_content:
        pass  # No change, skip write to avoid triggering file watcher
    else:
        data_path.write_text(new_content, encoding="utf-8")

    # Generate stub pages for agents that don't have a docs page yet
    agents_docs_dir = Path(config["docs_dir"]) / "custom-agents"
    agents_docs_dir.mkdir(parents=True, exist_ok=True)

    for agent in catalog:
        doc_path = agents_docs_dir / f"{agent['id']}.md"
        _generate_agent_stub(doc_path, agent, config_dir)

    # Inject agent pages into nav in memory
    _update_nav(config, catalog)


def _build_agents_catalog(config_dir: str) -> list:
    """Scan all custom agents and build catalog entries."""
    agents_dir = Path(config_dir) / "custom-agents"
    catalog = []

    if not agents_dir.is_dir():
        return catalog

    for agent_path in sorted(agents_dir.iterdir()):
        if not agent_path.is_dir():
            continue

        readme = agent_path / "README.md"
        if not readme.is_file():
            continue

        readme_text = readme.read_text(encoding="utf-8")
        lines = readme_text.split("\n")

        # Extract title from first heading (strip " — Custom Agent" suffix)
        name = _format_name(agent_path.name)
        for line in lines:
            if line.startswith("# "):
                raw_title = line[2:].strip()
                raw_title = re.sub(r'\s*[—–-]\s*Custom Agent\s*$', '', raw_title)
                name = raw_title
                break

        # Extract description from the Purpose section
        description = _extract_section_first_paragraph(readme_text, "Purpose")
        if not description:
            # Fallback: first paragraph after title
            past_title = False
            for line in lines:
                if line.startswith("# "):
                    past_title = True
                    continue
                if past_title and line.strip() and not line.startswith("#"):
                    description = line.strip()
                    break

        # Convert markdown links/bold to HTML for card rendering
        description = re.sub(
            r'\[([^\]]+)\]\(([^)]+)\)',
            r'<a href="\2" target="_blank" rel="noopener">\1</a>',
            description
        )
        description = re.sub(
            r'\*\*([^*]+)\*\*',
            r'<strong>\1</strong>',
            description
        )

        # Extract tools and skills from the README
        tools = _extract_tools(readme_text)
        skills = _extract_skills(readme_text)

        catalog.append({
            "id": agent_path.name,
            "name": name,
            "description": description,
            "tools": tools,
            "skills": skills,
        })

    return catalog


def _extract_section_first_paragraph(text: str, section_name: str) -> str:
    """Extract the first paragraph from a named ## section."""
    pattern = rf'^## {re.escape(section_name)}\s*$'
    lines = text.split("\n")
    in_section = False
    paragraph_lines = []

    for line in lines:
        if re.match(pattern, line, re.MULTILINE):
            in_section = True
            continue
        if in_section:
            if line.startswith("## "):
                break
            if line.strip() == "" and paragraph_lines:
                break
            if line.strip():
                paragraph_lines.append(line.strip())

    return " ".join(paragraph_lines)


def _extract_tools(readme_text: str) -> list:
    """Extract tool names from the Creating the Agent section."""
    creating_section = _extract_full_section(readme_text, "Creating the Agent")
    if creating_section:
        tool_matches = re.findall(r'`(use_\w+)`', creating_section)
        return list(dict.fromkeys(tool_matches))  # Dedupe preserving order
    return []


def _extract_skills(readme_text: str) -> list:
    """Extract skill names from Prerequisites section links."""
    prereq_section = _extract_full_section(readme_text, "Prerequisites")
    if prereq_section:
        skill_matches = re.findall(r'\.\./\.\./skills/([\w-]+)/', prereq_section)
        return list(dict.fromkeys(skill_matches))
    return []


def _extract_full_section(text: str, section_name: str) -> str:
    """Extract all text from a named ## section until the next ## heading."""
    pattern = rf'^## {re.escape(section_name)}\s*$'
    lines = text.split("\n")
    in_section = False
    section_lines = []

    for line in lines:
        if re.match(pattern, line, re.MULTILINE):
            in_section = True
            continue
        if in_section:
            if line.startswith("## "):
                break
            section_lines.append(line)

    return "\n".join(section_lines)


def _format_name(agent_id: str) -> str:
    """Convert agent-id to display name: aws-health-report -> AWS Health Report."""
    words = agent_id.split("-")
    acronyms = {"aws", "eks", "rds", "rca", "mcp", "crm"}
    return " ".join(
        w.upper() if w.lower() in acronyms else w.capitalize()
        for w in words
    )


def _generate_agent_stub(doc_path: Path, agent: dict, config_dir: str):
    """Generate a custom agent doc page from its README (only if changed)."""
    repo_url = "https://github.com/aws-samples/sample-devops-agent-tools"
    github_link = (
        f'<a href="{repo_url}/tree/main/custom-agents/{agent["id"]}" '
        f'target="_blank" rel="noopener" class="md-button">'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16" '
        f'style="vertical-align: text-bottom; margin-right: 0.3rem;">'
        f'<path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59'
        f'.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48'
        f'-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33'
        f'.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2'
        f'-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 '
        f'1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07'
        f'-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55'
        f'.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>'
        f'View on GitHub</a>\n\n'
    )

    # Build metadata block (tools + skills tags)
    meta_block = _build_agent_meta_block(agent)

    readme = Path(config_dir) / "custom-agents" / agent["id"] / "README.md"
    if readme.is_file():
        readme_content = readme.read_text(encoding="utf-8")
        # Insert the GitHub link and metadata after the first heading
        lines = readme_content.split("\n", 1)
        if len(lines) == 2 and lines[0].startswith("# "):
            content = lines[0] + "\n\n" + github_link + meta_block + lines[1]
        else:
            content = github_link + meta_block + readme_content
    else:
        content = f"# {agent['name']}\n\n{github_link}{meta_block}{agent['description']}\n"

    if doc_path.is_file() and doc_path.read_text(encoding="utf-8") == content:
        return  # No change, skip write
    doc_path.write_text(content, encoding="utf-8")


def _build_agent_meta_block(agent: dict) -> str:
    """Build an HTML block showing tools and skills tags for an agent page."""
    tags_html = []

    for tool in agent.get("tools", []):
        tags_html.append(f'<span class="tag tag-tool">{tool}</span>')

    for skill in agent.get("skills", []):
        tags_html.append(f'<span class="tag tag-skill">{skill}</span>')

    if tags_html:
        return (
            '<div class="skill-page-meta">\n'
            '<div class="skill-tags">' + " ".join(tags_html) + '</div>\n'
            '</div>\n\n'
        )
    return ""


def _update_nav(config, catalog):
    """Inject custom agent pages into the nav in memory.

    Looks for Custom Agents > Catalog section and appends agent pages there.
    """
    nav = config.get("nav")
    if not nav:
        return

    for item in nav:
        if isinstance(item, dict) and "Custom Agents" in item:
            agents_nav = item["Custom Agents"]

            for entry in agents_nav:
                if isinstance(entry, dict) and "Catalog" in entry:
                    catalog_nav = entry["Catalog"]

                    # Collect existing paths
                    existing_paths = set()
                    for sub in catalog_nav:
                        if isinstance(sub, dict):
                            for path in sub.values():
                                existing_paths.add(path)
                        elif isinstance(sub, str):
                            existing_paths.add(sub)

                    # Add agent pages
                    for agent in catalog:
                        page_path = f"custom-agents/{agent['id']}.md"
                        if page_path not in existing_paths:
                            catalog_nav.append({agent["name"]: page_path})
                    break
            break
