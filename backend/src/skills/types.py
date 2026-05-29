from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, field_serializer

from src.schema import CapyBaseModel


class Skill(CapyBaseModel):
    """Represents a skill with its metadata and file path"""

    name: str = Field(..., description="Skill name from SKILL.md frontmatter")
    description: str = Field(..., description="Short description from SKILL.md frontmatter")
    license: str | None = Field(default=None, description="Optional license metadata from SKILL.md frontmatter")
    skill_dir: Path = Field(..., description="Host path to the skill directory")
    skill_file: Path = Field(..., description="Host path to the skill's SKILL.md file")
    relative_path: Path = Field(..., description="Path from the category root to the skill directory")
    category: Literal["public", "custom"] = Field(..., description="Skill source category")
    enabled: bool = Field(default=False, description="Whether this skill is enabled")
    paths: list[str] | None = Field(default=None, description="Optional matcher patterns for auto-activation")
    workflow: bool = Field(default=False, description="Whether this skill is an intentional batch-workflow skill")

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    @field_serializer("skill_dir", "skill_file", "relative_path")
    def _serialize_path(self, value: Path) -> str:
        return value.as_posix()

    @property
    def skill_path(self) -> str:
        """Returns the relative path from the category root (skills/{category}) to this skill's directory"""
        path = self.relative_path.as_posix()
        return "" if path == "." else path

    def get_container_path(self, container_base_path: str = "/mnt/skills") -> str:
        """
        Get the full path to this skill in the container.

        Args:
            container_base_path: Base path where skills are mounted in the container

        Returns:
            Full container path to the skill directory
        """
        if self.category == "custom":
            category_base = f"{container_base_path}/custom"
            skill_path = self.skill_path
            if skill_path:
                return f"{category_base}/{skill_path}"
            return category_base

        # Public skills use flat layout directly under container_base_path.
        skill_path = self.skill_path
        if skill_path:
            return f"{container_base_path}/{skill_path}"
        return container_base_path

    def get_container_file_path(self, container_base_path: str = "/mnt/skills") -> str:
        """
        Get the full path to this skill's main file (SKILL.md) in the container.

        Args:
            container_base_path: Base path where skills are mounted in the container

        Returns:
            Full container path to the skill's SKILL.md file
        """
        return f"{self.get_container_path(container_base_path)}/SKILL.md"

    def __repr__(self) -> str:
        return f"Skill(name={self.name!r}, description={self.description!r}, category={self.category!r})"
