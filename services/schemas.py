"""
services/schemas.py

Single source of truth on the bot side for the Firestore document shapes.
Mirrors the canonical contract defined by the dashboard repo:
  https://github.com/cpcap1214/IM2008/blob/main/src/lib/firestore.ts

These dataclasses + validators are intentionally lightweight (no pydantic
dependency) so we don't add anything new to requirements.txt. They are
called from `services/firebase_db.py` before every `.set()` / `.update()`
to reject malformed payloads with a clear `ValueError`.

Notes on Firestore sentinels:
- `firestore.SERVER_TIMESTAMP` is a sentinel object, NOT a `datetime`.
  Validators treat it as a valid value for timestamp fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, List, Optional


# ---------------------------------------------------------------------------
# Allowed enums (mirror dashboard contract)
# ---------------------------------------------------------------------------
USER_ROLES = ("boss", "driver", "customer")
PAYMENT_STATUSES = ("unpaid", "paid", "pending_confirmation")
PAYMENT_METHODS = ("cash", "transfer", "check")


def _is_timestamp_like(value: Any) -> bool:
    """
    Accepts:
      - datetime instances
      - the firestore.SERVER_TIMESTAMP sentinel (or any sentinel-like object)
      - None  (caller decides whether None is allowed; validators check that
        separately via the `nullable` flag)
    """
    if value is None:
        return True
    if isinstance(value, datetime):
        return True
    # SERVER_TIMESTAMP is a sentinel; we accept any non-primitive object that
    # is not obviously the wrong type. Be permissive but reject str/int/float.
    if isinstance(value, (str, int, float, bool, list, dict)):
        return False
    return True


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(f"Schema validation failed: {msg}")


# ---------------------------------------------------------------------------
# OrderItem
# ---------------------------------------------------------------------------
@dataclass
class OrderItem:
    productName: str
    spec: str
    quantity: int
    unitPrice: int
    subtotal: int

    def validate(self) -> None:
        _require(isinstance(self.productName, str) and self.productName, "OrderItem.productName must be a non-empty str")
        _require(isinstance(self.spec, str), "OrderItem.spec must be a str (use '' when missing, not None)")
        _require(isinstance(self.quantity, int) and self.quantity > 0, "OrderItem.quantity must be a positive int")
        _require(isinstance(self.unitPrice, int) and self.unitPrice >= 0, "OrderItem.unitPrice must be a non-negative int")
        _require(isinstance(self.subtotal, int) and self.subtotal >= 0, "OrderItem.subtotal must be a non-negative int")
        _require(self.subtotal == self.unitPrice * self.quantity,
                 f"OrderItem.subtotal ({self.subtotal}) must equal unitPrice*quantity ({self.unitPrice * self.quantity})")

    def to_dict(self) -> dict:
        self.validate()
        return asdict(self)


# ---------------------------------------------------------------------------
# OrderDoc
# ---------------------------------------------------------------------------
@dataclass
class OrderDoc:
    customerId: str
    customerName: str
    driverId: Optional[str]                # None when unassigned (NEVER "尚未指派")
    items: List[OrderItem]
    totalAmount: int
    paymentStatus: str                     # one of PAYMENT_STATUSES
    paymentMethod: Optional[str]           # None unless paymentStatus == 'paid'
    orderDate: Any                         # datetime | SERVER_TIMESTAMP sentinel
    deliveryDate: Any = None               # datetime | None ; only mark_delivered sets it
    paidAt: Any = None                     # datetime | SERVER_TIMESTAMP | None
    createdAt: Any = None                  # datetime | SERVER_TIMESTAMP | None

    def validate(self) -> None:
        _require(isinstance(self.customerId, str) and self.customerId, "OrderDoc.customerId must be a non-empty str")
        _require(isinstance(self.customerName, str), "OrderDoc.customerName must be a str")
        _require(self.driverId is None or (isinstance(self.driverId, str) and self.driverId),
                 "OrderDoc.driverId must be a non-empty str or None (never the literal '尚未指派')")
        _require(self.driverId != "尚未指派", "OrderDoc.driverId must be None when unassigned, not the literal '尚未指派'")

        _require(isinstance(self.items, list) and len(self.items) > 0, "OrderDoc.items must be a non-empty list")
        items_sum = 0
        for idx, it in enumerate(self.items):
            _require(isinstance(it, OrderItem), f"OrderDoc.items[{idx}] must be an OrderItem instance")
            it.validate()
            items_sum += it.subtotal
        _require(isinstance(self.totalAmount, int) and self.totalAmount >= 0,
                 "OrderDoc.totalAmount must be a non-negative int")
        _require(self.totalAmount == items_sum,
                 f"OrderDoc.totalAmount ({self.totalAmount}) must equal sum(items[].subtotal) ({items_sum})")

        _require(self.paymentStatus in PAYMENT_STATUSES,
                 f"OrderDoc.paymentStatus must be one of {PAYMENT_STATUSES}, got {self.paymentStatus!r}")
        if self.paymentStatus == "paid":
            _require(self.paymentMethod in PAYMENT_METHODS,
                     f"paid orders require paymentMethod in {PAYMENT_METHODS}, got {self.paymentMethod!r}")
        else:
            _require(self.paymentMethod is None,
                     "paymentMethod MUST be None when paymentStatus != 'paid'")

        _require(_is_timestamp_like(self.orderDate) and self.orderDate is not None,
                 "OrderDoc.orderDate must be a datetime or Firestore sentinel (not None)")
        _require(_is_timestamp_like(self.deliveryDate),
                 "OrderDoc.deliveryDate must be a datetime, Firestore sentinel, or None")
        _require(_is_timestamp_like(self.paidAt),
                 "OrderDoc.paidAt must be a datetime, Firestore sentinel, or None")
        _require(_is_timestamp_like(self.createdAt),
                 "OrderDoc.createdAt must be a datetime, Firestore sentinel, or None")

    def to_dict(self) -> dict:
        self.validate()
        d = {
            "customerId": self.customerId,
            "customerName": self.customerName,
            "driverId": self.driverId,
            "items": [it.to_dict() for it in self.items],
            "totalAmount": self.totalAmount,
            "paymentStatus": self.paymentStatus,
            "paymentMethod": self.paymentMethod,
            "orderDate": self.orderDate,
            "deliveryDate": self.deliveryDate,
        }
        if self.paidAt is not None:
            d["paidAt"] = self.paidAt
        if self.createdAt is not None:
            d["createdAt"] = self.createdAt
        return d


# ---------------------------------------------------------------------------
# UserDoc
# ---------------------------------------------------------------------------
@dataclass
class UserDoc:
    role: str
    displayName: str
    phone: str = ""
    notes: str = ""
    createdAt: Any = None  # datetime | SERVER_TIMESTAMP | None

    def validate(self) -> None:
        _require(self.role in USER_ROLES, f"UserDoc.role must be one of {USER_ROLES}, got {self.role!r}")
        _require(isinstance(self.displayName, str) and self.displayName, "UserDoc.displayName must be a non-empty str")
        _require(isinstance(self.phone, str), "UserDoc.phone must be a str ('' when missing)")
        _require(isinstance(self.notes, str), "UserDoc.notes must be a str ('' when missing)")
        _require(_is_timestamp_like(self.createdAt),
                 "UserDoc.createdAt must be a datetime, Firestore sentinel, or None")

    def to_dict(self) -> dict:
        self.validate()
        d = {
            "role": self.role,
            "displayName": self.displayName,
            "phone": self.phone,
            "notes": self.notes,
        }
        if self.createdAt is not None:
            d["createdAt"] = self.createdAt
        return d


# ---------------------------------------------------------------------------
# Payment-update patch validator (no dataclass; it's a partial update)
# ---------------------------------------------------------------------------
def validate_payment_update_patch(patch: dict) -> None:
    """
    Validate the partial-update dict passed to Orders.update() for payment
    transitions performed by `update_order_payment` / `confirm_payment`.

    Required keys: paymentStatus.
    Rules:
      - paymentStatus must be in PAYMENT_STATUSES.
      - When paymentStatus == 'paid': paymentMethod must be a valid method
        and paidAt must be present.
      - When paymentStatus != 'paid': paymentMethod must be None and
        paidAt must be None (if present).
      - 'deliveryDate' MUST NOT appear in payment update patches — only
        `mark_delivered` may write it.
      - driverId, if present, must be a non-empty string (never "尚未指派").
    """
    _require("paymentStatus" in patch, "payment patch must include 'paymentStatus'")
    status = patch["paymentStatus"]
    _require(status in PAYMENT_STATUSES,
             f"paymentStatus must be one of {PAYMENT_STATUSES}, got {status!r}")

    _require("deliveryDate" not in patch,
             "payment patches must NOT touch deliveryDate (use mark_delivered)")

    if status == "paid":
        _require(patch.get("paymentMethod") in PAYMENT_METHODS,
                 f"paid transition requires paymentMethod in {PAYMENT_METHODS}")
        _require("paidAt" in patch and patch["paidAt"] is not None,
                 "paid transition requires paidAt to be set")
    else:
        _require(patch.get("paymentMethod", None) is None,
                 "non-paid payment patches must set paymentMethod to None")
        if "paidAt" in patch:
            _require(patch["paidAt"] is None,
                     "non-paid payment patches must set paidAt to None (or omit it)")

    if "driverId" in patch:
        drv = patch["driverId"]
        _require(drv is None or (isinstance(drv, str) and drv and drv != "尚未指派"),
                 "driverId in payment patch must be None or a non-empty str (never '尚未指派')")
