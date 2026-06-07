"""
MkDocs hook: generates the skills catalog data from SKILL.md frontmatter.

At build time, scans skills/*/SKILL.md, extracts metadata, and writes
docs/javascripts/skills-data.json. The JS on the catalog page reads this
JSON to render cards and group-by buttons dynamically.

This also generates individual skill doc stubs if a docs/skills/<name>.md
doesn't already exist, so new skills appear on the site automatically.
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
    acronyms = {"aws", "eks", "rds", "rca", "mcp"}
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
        if not doc_path.is_file():
            _generate_skill_stub(doc_path, skill, config_dir)

    # Inject skill pages into nav in memory
    _update_nav(config, catalog)


def _generate_skill_stub(doc_path: Path, skill: dict, config_dir: str):
    """Generate a minimal skill doc page from its README (only if changed)."""
    readme = Path(config_dir) / "skills" / skill["id"] / "README.md"
    if readme.is_file():
        content = readme.read_text(encoding="utf-8")
    else:
        content = f"# {skill['name']}\n\n{skill['description']}\n"

    if doc_path.is_file() and doc_path.read_text(encoding="utf-8") == content:
        return  # No change, skip write
    doc_path.write_text(content, encoding="utf-8")


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
