"""
Skill Creator — auto-creates and manages SKILL.md files in data/skills/.
Hermes-inspired: each skill encodes tactical knowledge with YAML frontmatter.
"""
import os
import re
import yaml
from datetime import datetime
from typing import Optional

SKILLS_DIR = "data/skills"


class SkillCreator:
    """Manages SKILL.md files — create, patch, list, and format for LLM injection."""

    def __init__(self, skills_dir: str = SKILLS_DIR):
        self.skills_dir = skills_dir
        os.makedirs(self.skills_dir, exist_ok=True)

    def create_skill(self, name: str, description: str, tags: list[str],
                     regime: Optional[str] = None, signal: Optional[str] = None,
                     procedure: str = "", pitfalls: str = "",
                     evidence: str = "") -> str:
        """Create a new SKILL.md with YAML frontmatter and markdown body.
        Returns the skill filename on success, raises ValueError if duplicate."""
        safe_name = re.sub(r'[^a-z0-9_-]', '_', name.lower().strip())
        filepath = os.path.join(self.skills_dir, f"{safe_name}.md")
        if os.path.exists(filepath):
            raise ValueError(f"Skill '{safe_name}' already exists")

        frontmatter = {
            "name": name,
            "description": description,
            "version": "1.0.0",
            "tags": tags,
            "regime": regime or "",
            "signal": signal or "",
            "evidence": evidence or "",
            "created_at": datetime.now().isoformat(),
        }

        lines = ["---"]
        lines.append(yaml.dump(frontmatter, default_flow_style=False, sort_keys=False).rstrip())
        lines.append("---")
        lines.append("")
        lines.append("## When to Use")
        lines.append(f"Regime: {regime or 'Any'} | Signal: {signal or 'Any'}")
        lines.append("")
        lines.append("## Procedure")
        lines.append(procedure or "_No procedure defined yet._")
        lines.append("")
        lines.append("## Pitfalls")
        lines.append(pitfalls or "_No pitfalls recorded._")
        lines.append("")
        lines.append("## Evidence")
        lines.append(evidence or "_No evidence recorded yet._")

        with open(filepath, "w") as f:
            f.write("\n".join(lines) + "\n")

        return f"{safe_name}.md"

    def patch_skill(self, name: str, old_string: str, new_string: str) -> bool:
        """Targeted update to an existing skill by replacing old_string with new_string."""
        safe_name = re.sub(r'[^a-z0-9_-]', '_', name.lower().strip())
        filepath = os.path.join(self.skills_dir, f"{safe_name}.md")
        if not os.path.exists(filepath):
            return False
        with open(filepath, "r") as f:
            content = f.read()
        if old_string not in content:
            return False
        content = content.replace(old_string, new_string, 1)
        with open(filepath, "w") as f:
            f.write(content)
        return True

    def list_skills(self, regime: Optional[str] = None,
                    signal: Optional[str] = None) -> list[dict]:
        """List all skills, optionally filtered by regime or signal."""
        skills = []
        if not os.path.isdir(self.skills_dir):
            return skills
        for filename in sorted(os.listdir(self.skills_dir)):
            if not filename.endswith(".md"):
                continue
            try:
                skill = self.get_skill(filename.replace(".md", ""))
                if skill is None:
                    continue
                if regime and skill.get("regime", "").upper() != regime.upper():
                    continue
                if signal and skill.get("signal", "").upper() != signal.upper():
                    continue
                skills.append(skill)
            except Exception:
                continue
        return skills

    def get_skill(self, name: str) -> Optional[dict]:
        """Parse a SKILL.md and return dict with frontmatter + body."""
        safe_name = re.sub(r'[^a-z0-9_-]', '_', name.lower().strip())
        filepath = os.path.join(self.skills_dir, f"{safe_name}.md")
        if not os.path.exists(filepath):
            return None
        with open(filepath, "r") as f:
            content = f.read()

        match = re.match(r'^---\n(.*?)\n---\n(.*)', content, re.DOTALL)
        if not match:
            return None

        frontmatter = yaml.safe_load(match.group(1)) or {}
        body = match.group(2).strip()
        return {**frontmatter, "body": body, "filename": f"{safe_name}.md"}

    def get_skills_for_prompt(self, limit: int = 6) -> str:
        """Return formatted skills for LLM system prompt injection."""
        all_skills = self.list_skills()
        if not all_skills:
            return ""

        selected = all_skills[:limit]
        lines = ["## Agent Skills"]
        for s in selected:
            name = s.get("name", "Unknown")
            desc = s.get("description", "")
            regime = s.get("regime", "")
            signal = s.get("signal", "")
            evidence = s.get("evidence", "")
            lines.append(f"### {name}")
            lines.append(f"**{desc}**")
            if regime or signal:
                lines.append(f"Regime: {regime or 'Any'} | Signal: {signal or 'Any'}")
            if evidence:
                lines.append(f"Evidence: {evidence}")
            lines.append("")

        return "\n".join(lines)
