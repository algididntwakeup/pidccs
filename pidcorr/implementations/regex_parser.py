from typing import Dict, Any, Optional
from ..interfaces.perception import BasePipingIDParser
from ..piping_id import parse_tokens, remember_schema, match_pid


class RegexPipingIDParser(BasePipingIDParser):
    """Cascading regex parser for standard company schemas (PetroChina, Pertamina) and user-taught schemas."""

    def parse(self, pid_text: str) -> Optional[Dict[str, str]]:
        parsed = parse_tokens(pid_text)
        if any(parsed.values()):
            return parsed
        # Fallback check if it matched
        canonical, _ = match_pid(pid_text)
        if canonical:
            return parse_tokens(canonical)
        return None

    def register_schema(self, example_pid: str, schema: Dict[str, Any]) -> None:
        remember_schema(example_pid, schema)

