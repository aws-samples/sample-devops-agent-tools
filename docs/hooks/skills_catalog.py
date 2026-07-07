"""
MkDocs hook: generates catalog data for skills and custom agents.

At build time:
- Scans skills/*/SKILL.md, extracts metadata, and writes
  docs/javascripts/skills-data.json.
- Scans custom-agents/*/README.md, extracts metadata, and writes
  docs/javascripts/agents-data.json.

The JS on the catalog pages reads these JSON files to render cards
and group-by buttons dynamically.

This also generates individual doc stubs for skills and custom agents
that don't have a docs page yet, so new entries appear on the site
automatically.
"""

import json
import re
from pathlib import Path


def _parse_frontmatter(path: Path) -> dict:
    """Extract YAML frontmatter from a SKILL.md file (minimal parser, no deps)."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}

    end = text.find("---", 3)
    if end == -1:
        return {}

    fm_text = text[3:end]
    result = {"_body": text[end + 3:].strip()}

    # Parse top-level keys (name, description)
    current_key = None
    current_value = ""

    for line in fm_text.splitlines():
        # Top-level key: value
        m = re.match(r'^(\w[\w\-.]*):\s*(.*)', line)
        if m and not line.startswith("  "):
            if current_key:
                result[current_key] = current_value.strip()
            current_key = m.group(1)
            current_value = m.group(2)
        elif current_key and line.startswith("  "):
            # Continuation or nested
            current_value += " " + line.strip()

    if current_key:
        result[current_key] = current_value.strip()

    # Parse metadata block specifically
    metadata = {}
    in_metadata = False
    for line in fm_text.splitlines():
        if line.strip() == "metadata:":
            in_metadata = True
            continue
        if in_metadata:
            if line and not line.startswith(" "):
                break
            m = re.match(r'^\s+([\w\-.]+ *):\s*"?([^"]*)"?\s*$', line)
            if m:
                metadata[m.group(1).strip()] = m.group(2).strip()

    result["metadata"] = metadata
    return result


def _build_catalog(config_dir: str) -> list:
    """Scan all skills and build catalog entries."""
    skills_dir = Path(config_dir) / "skills"
    catalog = []

    if not skills_dir.is_dir():
        return catalog

    for skill_path in sorted(skills_dir.iterdir()):
        if not skill_path.is_dir():
            continue

        skill_md = skill_path / "SKILL.md"
        readme = skill_path / "README.md"

        if not skill_md.is_file():
            continue

        fm = _parse_frontmatter(skill_md)
        metadata = fm.get("metadata", {})
        name = fm.get("name", skill_path.name)

        # Extract description from README first line after title, or from frontmatter
        description = ""
        if readme.is_file():
            readme_text = readme.read_text(encoding="utf-8")
            # Find first paragraph after the title
            lines = readme_text.split("\n")
            past_title = False
            for line in lines:
                if line.startswith("# "):
                    past_title = True
                    continue
                if past_title and line.strip() and not line.startswith("#"):
                    description = line.strip()
                    break

        if not description:
            description = fm.get("description", "")[:200]

        # Convert markdown links to HTML (rendered inside innerHTML in cards)
        description = re.sub(
            r'\[([^\]]+)\]\(([^)]+)\)',
            r'<a href="\2" target="_blank" rel="noopener">\1</a>',
            description
        )
        # Convert markdown bold to HTML
        description = re.sub(
            r'\*\*([^*]+)\*\*',
            r'<strong>\1</strong>',
            description
        )

        # Collect all aws-devops-agent-skills.* dimensions
        dimensions = {}
        for key, value in metadata.items():
            if key.startswith("aws-devops-agent-skills."):
                dim_name = key.replace("aws-devops-agent-skills.", "")
                dimensions[dim_name] = [v.strip() for v in value.split(",")]

        catalog.append({
            "id": skill_path.name,
            "name": _format_name(skill_path.name),
            "description": description,
            "dimensions": dimensions,
            "author": metadata.get("author", ""),
            "version": metadata.get("version", ""),
        })

    return catalog


def _format_name(skill_id: str) -> str:
    """Convert skill-id to display name: aws-health-events -> AWS Health Events."""
    words = skill_id.split("-")
    # Capitalize known acronyms
    acronyms = {"aws", "eks", "rds", "rca", "mcp", "crm"}
    return " ".join(
        w.upper() if w.lower() in acronyms else w.capitalize()
        for w in words
    )


def on_pre_build(config, **kwargs):
    """Generate skills-data.json and skill doc stubs before the build."""
    config_dir = config["docs_dir"].replace("/docs", "")
    catalog = _build_catalog(config_dir)

    # Write JSON for the JS to consume (only if changed, to avoid dev-server loop)
    js_dir = Path(config["docs_dir"]) / "javascripts"
    js_dir.mkdir(parents=True, exist_ok=True)
    data_path = js_dir / "skills-data.json"
    new_content = json.dumps(catalog, indent=2)
    if data_path.is_file() and data_path.read_text(encoding="utf-8") == new_content:
        pass  # No change, skip write to avoid triggering file watcher
    else:
        data_path.write_text(new_content, encoding="utf-8")

    # Generate stub pages for skills that don't have a docs page yet
    skills_docs_dir = Path(config["docs_dir"]) / "skills"
    skills_docs_dir.mkdir(parents=True, exist_ok=True)

    for skill in catalog:
        doc_path = skills_docs_dir / f"{skill['id']}.md"
        _generate_skill_stub(doc_path, skill, config_dir)

    # Inject skill pages into nav in memory
    _update_nav(config, catalog)


def _generate_skill_stub(doc_path: Path, skill: dict, config_dir: str):
    """Generate a minimal skill doc page from its README (only if changed)."""
    repo_url = "https://github.com/aws-samples/sample-devops-agent-tools"
    github_link = (
        f'<a href="{repo_url}/tree/main/skills/{skill["id"]}" '
        f'target="_blank" rel="noopener" class="md-button">'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16" style="vertical-align: text-bottom; margin-right: 0.3rem;"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>'
        f'View on GitHub</a>\n\n'
    )

    # Build metadata block (author + dimension tags)
    meta_block = _build_skill_meta_block(skill)

    readme = Path(config_dir) / "skills" / skill["id"] / "README.md"
    if readme.is_file():
        readme_content = readme.read_text(encoding="utf-8")
        # Insert the GitHub link and metadata after the first heading
        lines = readme_content.split("\n", 1)
        if len(lines) == 2 and lines[0].startswith("# "):
            content = lines[0] + "\n\n" + github_link + meta_block + lines[1]
        else:
            content = github_link + meta_block + readme_content
    else:
        content = f"# {skill['name']}\n\n{github_link}{meta_block}{skill['description']}\n"

    if doc_path.is_file() and doc_path.read_text(encoding="utf-8") == content:
        return  # No change, skip write
    doc_path.write_text(content, encoding="utf-8")


def _build_skill_meta_block(skill: dict) -> str:
    """Build an HTML block showing author and dimension tags for a skill page."""
    parts = []

    # Author line (linked to GitHub profile)
    author = skill.get("author", "")
    if author:
        parts.append(
            f'<div class="skill-meta">'
            f'<span class="skill-author">by <a href="https://github.com/{author}" '
            f'target="_blank" rel="noopener"><strong>{author}</strong></a></span>'
            f'</div>'
        )

    # Dimension tags
    dimensions = skill.get("dimensions", {})
    tags_html = []
    tag_class_map = {
        "agent-types": "tag-agent",
        "aws-services": "tag-service",
        "technical-domains": "tag-domain",
    }
    for dim_key, values in dimensions.items():
        css_class = tag_class_map.get(dim_key, "tag-domain")
        for value in values:
            tags_html.append(f'<span class="tag {css_class}">{value}</span>')

    if tags_html:
        parts.append(
            '<div class="skill-tags">' + " ".join(tags_html) + '</div>'
        )

    if parts:
        return '<div class="skill-page-meta">\n' + "\n".join(parts) + "\n</div>\n\n"
    return ""


def _update_nav(config, catalog):
    """Inject skill pages into the nav in memory (no file writes).
    
    Looks for Skills > Catalog section and appends skill pages there.
    """
    nav = config.get("nav")
    if not nav:
        return

    # Find the Skills section
    for item in nav:
        if isinstance(item, dict) and "Skills" in item:
            skills_nav = item["Skills"]

            # Find the Catalog subsection
            for entry in skills_nav:
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

                    # Add skill pages
                    for skill in catalog:
                        page_path = f"skills/{skill['id']}.md"
                        if page_path not in existing_paths:
                            catalog_nav.append({skill["name"]: page_path})
                    break
            break
