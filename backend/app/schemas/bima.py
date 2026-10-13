from pydantic import BaseModel, Field

class BimaProjectRecord(BaseModel):
    """One row of BIMA's flat project catalog.

    BIMA owns no assignment or role data per project: the catalog is a flat
    directory and the engineer who imports a row becomes its local owner.
    """

    bima_project_id: str = Field(min_length=1, max_length=255)
    project_number: str = Field(default="", max_length=64)
    name: str = Field(min_length=1, max_length=255)

class BimaIdentity(BaseModel):
    bima_user_id: str
    email: str
    full_name: str
    department: str
    is_bima_superuser: bool = False

class ProjectImportRequest(BaseModel):
    bima_project_id: str = Field(min_length=1, max_length=255)
    project_number: str = Field(default="", max_length=64)
    name: str = Field(min_length=1, max_length=255)
