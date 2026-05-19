"""
Skill Manager — manages skills as Markdown files in vault/skills/.
Migrated from skill_creator.py with FTS5 indexing and wikilink support.
"""
import os
import re
import yaml
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class SkillManager:
    """Manages skills in vault/skills/ as Markdown files with YAML frontmatter."""

    def __init__(self, vault_dir: str = "vault", indexer=None):
        self.skills_dir = os.path.join(vault_dir, "skills")
        os.makedirs(self.skills_dir, exist_ok=True)
        self.indexer = indexer

    def create_skill(self, name: str, description: str, tags: List[str],
                     regime: Optional[str] = None, signal: Optional[str] = None,
                     procedure: str = "", pitfalls: str = "",
                     evidence: str = "", related: Optional[List[str]] = None) -> str:
        """Create a new skill as Markdown file. Returns filename."""
        safe_name = re.sub(r'[^a-z0-9_-]', '_', name.lower().strip())
        filepath = os.path.join(self.skills_dir, f"{safe_name}.md")

        if os.path.exists(filepath):
            # Patch existing skill instead of failing
            return self._patch_skill(filepath, name, description, tags,
                                     regime, signal, procedure, pitfalls, evidence, related)

        frontmatter = {
            "title": name,
            "description": description,
            "version": "1.0.0",
            "tags": tags,
            "regime": regime or "Any",
            "signal": signal or "Any",
            "evidence": evidence or "",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }

        # Build related links section
        related_section = ""
        if related:
            related_section = "\n## Related\n"
            for r in related:
                related_section += f"[[{r}]]\n"

        body = f"""## When to Use
Regime: {regime or 'Any'} | Signal: {signal or 'Any'}

## Procedure
{procedure or '_No procedure defined yet._'}

## Pitfalls
{pitfalls or '_No pitfalls recorded._'}

## Evidence
{evidence or '_No evidence recorded yet._'}
{related_section}"""

        content = "---\n" + yaml.dump(frontmatter, default_flow_style=False, sort_keys=False).rstrip() + "\n---\n\n" + body

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        # Index if indexer available
        if self.indexer:
            rel_path = os.path.relpath(filepath, "vault")
            self.indexer.index_single(rel_path, filepath)

        logger.info(f"[SkillManager] Created skill: {safe_name}.md")
        return f"{safe_name}.md"

    def _patch_skill(self, filepath: str, name: str, description: str, tags: List[str],
                     regime: Optional[str], signal: Optional[str],
                     procedure: str, pitfalls: str, evidence: str,
                     related: Optional[List[str]]) -> str:
        """Update existing skill with new evidence."""
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        match = re.match(r'^---\n(.*?)\n---\n(.*)', content, re.DOTALL)
        if not match:
            return os.path.basename(filepath)

        frontmatter = yaml.safe_load(match.group(1)) or {}
        frontmatter["evidence"] = evidence
        frontmatter["updated_at"] = datetime.now().isoformat()
        if tags:
            frontmatter["tags"] = tags

        # Build related links section
        related_section = ""
        if related:
            related_section = "\n## Related\n"
            for r in related:
                related_section += f"[[{r}]]\n"

        body = f"""## When to Use
Regime: {regime or frontmatter.get('regime', 'Any')} | Signal: {signal or frontmatter.get('signal', 'Any')}

## Procedure
{procedure or '_No procedure defined yet._'}

## Pitfalls
{pitfalls or '_No pitfalls recorded._'}

## Evidence
{evidence or '_No evidence recorded yet._'}
{related_section}"""

        new_content = "---\n" + yaml.dump(frontmatter, default_flow_style=False, sort_keys=False).rstrip() + "\n---\n\n" + body

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)

        # Re-index
        if self.indexer:
            rel_path = os.path.relpath(filepath, "vault")
            self.indexer.index_single(rel_path, filepath)

        logger.info(f"[SkillManager] Patched skill: {os.path.basename(filepath)}")
        return os.path.basename(filepath)

    def get_skill(self, name: str) -> Optional[Dict]:
        """Get a skill by name (filename without .md)."""
        safe_name = re.sub(r'[^a-z0-9_-]', '_', name.lower().strip())
        filepath = os.path.join(self.skills_dir, f"{safe_name}.md")
        if not os.path.exists(filepath):
            return None

        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        match = re.match(r'^---\n(.*?)\n---\n(.*)', content, re.DOTALL)
        if not match:
            return None

        frontmatter = yaml.safe_load(match.group(1)) or {}
        body = match.group(2).strip()
        return {**frontmatter, "body": body, "filename": f"{safe_name}.md"}

    def list_skills(self, regime: Optional[str] = None,
                    signal: Optional[str] = None) -> List[Dict]:
        """List all skills, optionally filtered."""
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

    def get_skills_for_prompt(self, regime: Optional[str] = None,
                              signal: Optional[str] = None,
                              limit: int = 6) -> str:
        """Return formatted skills for LLM system prompt injection."""
        all_skills = self.list_skills(regime=regime, signal=signal)
        if not all_skills:
            return ""

        selected = all_skills[:limit]
        lines = ["## Agent Skills"]
        for s in selected:
            name = s.get("title", s.get("name", "Unknown"))
            desc = s.get("description", "")
            regime_val = s.get("regime", "")
            signal_val = s.get("signal", "")
            evidence = s.get("evidence", "")
            lines.append(f"### {name}")
            lines.append(f"**{desc}**")
            if regime_val or signal_val:
                lines.append(f"Regime: {regime_val or 'Any'} | Signal: {signal_val or 'Any'}")
            if evidence:
                lines.append(f"Evidence: {evidence}")
            lines.append("")

        return "\n".join(lines)

    def delete_skill(self, name: str) -> bool:
        """Delete a skill file."""
        safe_name = re.sub(r'[^a-z0-9_-]', '_', name.lower().strip())
        filepath = os.path.join(self.skills_dir, f"{safe_name}.md")
        if os.path.exists(filepath):
            os.remove(filepath)
            if self.indexer:
                rel_path = os.path.relpath(filepath, "vault")
                self.indexer.delete_document(rel_path)
            logger.info(f"[SkillManager] Deleted skill: {safe_name}.md")
            return True
        return False
