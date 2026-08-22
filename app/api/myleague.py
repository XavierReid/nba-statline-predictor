"""MyLeague router — mounts myleague_router under /myleague."""
from fastapi import APIRouter

from app.api.routes.myleague import myleague_router

router = APIRouter(prefix="/myleague", tags=["myleague"])
router.include_router(myleague_router)
