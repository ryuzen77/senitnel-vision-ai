from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check() -> dict:
    return {
        "status": "online",
        "service": "sentinel-vision-backend",
    }
