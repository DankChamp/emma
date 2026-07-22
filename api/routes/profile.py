from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_profile_manager
from core.profile import ProfileManager

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileEntry(BaseModel):
    category: str
    key: str
    value: str


class BulkProfile(BaseModel):
    category: str
    data: dict[str, str]


@router.get("")
def list_all(profile: ProfileManager = Depends(get_profile_manager)):
    return profile.get_all()


@router.get("/categories")
def list_categories(profile: ProfileManager = Depends(get_profile_manager)):
    return {"categories": profile.list_categories()}


@router.get("/{category}")
def get_category(category: str, profile: ProfileManager = Depends(get_profile_manager)):
    data = profile.get_category(category)
    return {"category": category, "data": data}


@router.post("")
def set_entry(entry: ProfileEntry, profile: ProfileManager = Depends(get_profile_manager)):
    profile.set(entry.category, entry.key, entry.value)
    return {"ok": True, "category": entry.category, "key": entry.key}


@router.post("/bulk")
def set_bulk(bulk: BulkProfile, profile: ProfileManager = Depends(get_profile_manager)):
    for key, value in bulk.data.items():
        profile.set(bulk.category, key, value)
    return {"ok": True, "category": bulk.category, "count": len(bulk.data)}


@router.delete("/{category}/{key}")
def delete_entry(category: str, key: str, profile: ProfileManager = Depends(get_profile_manager)):
    if not profile.delete(category, key):
        raise HTTPException(404, "Entry not found")
    return {"ok": True}
