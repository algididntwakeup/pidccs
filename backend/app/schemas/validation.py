from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field


class ValidationCheck(BaseModel):
    id: str
    name: str
    value: float
    detail: str
    passed: bool


class ValidationFlag(BaseModel):
    kind: str
    msg: Optional[str] = None
    msg_en: Optional[str] = None
    pid_idx: Optional[int] = None
    run_idx: Optional[int] = None
    fluid: Optional[str] = None
    suggest: Optional[str] = None


class ValidationReport(BaseModel):
    title: Optional[str] = "AUTOMATED VALIDATION"
    header: Optional[str] = ""
    n_symbols: int = 0
    n_piping_ids: int = 0
    n_pipes: int = 0
    score: float = 0.0
    passed: bool = False
    n_critical: int = 0
    n_warnings: int = 0
    checks: List[ValidationCheck] = Field(default_factory=list)
    flags: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[Dict[str, Any]] = Field(default_factory=list)
