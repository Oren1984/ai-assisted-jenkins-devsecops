"""Minimal sample service: something real for the Jenkins pipeline to build and test."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app import pricing

app = FastAPI(title="Sample Quote Service", version="1.0.0")


class QuoteRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=40)
    quantity: int = Field(ge=1, le=pricing.MAX_QUANTITY)
    discount_code: str | None = Field(default=None, max_length=40)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict:
    if not pricing.CATALOG:
        raise HTTPException(status_code=503, detail="catalog not loaded")
    return {"status": "ready", "catalog_items": len(pricing.CATALOG)}


@app.post("/quote")
def quote(request: QuoteRequest) -> dict:
    try:
        return pricing.calculate_quote(request.sku, request.quantity, request.discount_code)
    except pricing.UnknownSkuError:
        raise HTTPException(status_code=404, detail=f"unknown sku: {request.sku}") from None
    except pricing.UnknownDiscountCodeError:
        raise HTTPException(status_code=400, detail="unknown discount code") from None
