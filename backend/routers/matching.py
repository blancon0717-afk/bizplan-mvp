from fastapi import APIRouter
from pydantic import BaseModel

from core.matching import recommend

router = APIRouter()


class CompanyProfile(BaseModel):
    업력: str = "초기"
    아이템: str = ""
    청년: bool = False
    지역: str = "무관"


@router.post("/matching/recommend")
def recommend_programs(profile: CompanyProfile):
    results = recommend(profile.model_dump())
    return {"programs": results}
