from fastapi import APIRouter, Depends

from app.ocpi.common import VERSION, any_partner, base_url, ocpi_response

router = APIRouter(prefix="/ocpi", tags=["ocpi"], dependencies=[Depends(any_partner)])


@router.get("/versions")
def versions():
    return ocpi_response([{"version": VERSION, "url": f"{base_url()}/{VERSION}"}])


@router.get(f"/{VERSION}")
def version_details():
    return ocpi_response({
        "version": VERSION,
        "endpoints": [
            {"identifier": "credentials", "role": "SENDER", "url": f"{base_url()}/{VERSION}/credentials"},
            {"identifier": "credentials", "role": "RECEIVER", "url": f"{base_url()}/{VERSION}/credentials"},
            {"identifier": "tokens", "role": "SENDER", "url": f"{base_url()}/{VERSION}/tokens"},
        ],
    })
