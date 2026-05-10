from fastapi import APIRouter, HTTPException
from core.forms import load_form, list_programs

router = APIRouter(tags=["programs"])


@router.get("/programs")
def get_programs():
    programs = []
    for code, name in list_programs():
        try:
            form = load_form(code)
            programs.append({
                "code": form.program_code,
                "name": form.program_name,
                "target": form.target,
                "max_funding": form.max_funding,
                "page_limit": form.page_limit,
                "section_count": len(form.sections),
                "notes": form.notes,
            })
        except Exception:
            programs.append({"code": code, "name": name})
    return {"programs": programs}
