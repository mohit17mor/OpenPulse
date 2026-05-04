from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
import re
from typing import Any, Callable, Protocol
import urllib.request

from openpulse.checker import ExtractedValue


TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"
MAX_CANDIDATES_FOR_ADVISOR = 12
ID_KEY_RE = re.compile(r"(^id$|id$|uuid|guid|sku|key$|route|service|operator|product|listing|offer)", re.IGNORECASE)
IDENTITY_HINT_RE = re.compile(
    r"(name|title|time|arrival|departure|operator|route|service|product|sku|type|status)",
    re.IGNORECASE,
)
VALUE_KEY_RE = re.compile(r"(price|fare|amount|rate|cost|discount|seat|status|total|value)", re.IGNORECASE)
SENSITIVE_KEY_RE = re.compile(
    r"(authorization|cookie|token|secret|password|passwd|session|csrf|otp|jwt|bearer)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NetworkRecord:
    sequence: int
    captured_at: str
    url: str
    method: str
    status: int
    resource_type: str
    content_type: str
    post_data_sha256: str | None
    post_data_preview: str | None
    json_body: Any
    scalar_count: int

    def metadata(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "capturedAt": self.captured_at,
            "url": self.url,
            "method": self.method,
            "status": self.status,
            "resourceType": self.resource_type,
            "contentType": self.content_type,
            "postDataSha256": self.post_data_sha256,
            "postDataPreview": self.post_data_preview,
            "scalarCount": self.scalar_count,
        }


@dataclass(frozen=True)
class ScalarHit:
    path: str
    key: str | None
    value: Any
    item_path: str
    collection_path: str
    item: dict[str, Any]


class SetupAdvisor(Protocol):
    async def choose_recipe(self, packet: dict[str, Any]) -> dict[str, Any]:
        ...


class SmartSetupService:
    def __init__(self, advisor: SetupAdvisor | None = None):
        self.advisor = advisor

    async def prepare_selection(
        self,
        selection: dict[str, Any],
        records: list[NetworkRecord],
    ) -> dict[str, Any]:
        candidates = build_network_candidates(selection, records)
        packet = build_advisor_packet(selection, candidates)
        enriched = dict(selection)
        if self.advisor is None:
            enriched["sourceType"] = "dom"
            enriched["smartSetup"] = {
                "status": "advisor_unavailable",
                "domCandidate": packet["domCandidate"],
                "networkCandidateCount": len(candidates),
            }
            return enriched

        try:
            decision = await self.advisor.choose_recipe(packet)
        except Exception as exc:
            enriched["sourceType"] = "dom"
            enriched["smartSetup"] = {
                "status": "advisor_failed",
                "error": str(exc),
                "domCandidate": packet["domCandidate"],
                "networkCandidateCount": len(candidates),
            }
            return enriched

        enriched["smartSetup"] = {
            "status": "advised",
            "decision": sanitize_json_preview(decision, max_depth=3),
            "domCandidate": packet["domCandidate"],
            "networkCandidateCount": len(candidates),
        }
        if decision.get("source") != "network":
            enriched["sourceType"] = "dom"
            return enriched

        recipe = recipe_from_decision(decision, candidates)
        if recipe is None:
            enriched["sourceType"] = "dom"
            enriched["smartSetup"]["status"] = "recipe_rejected"
            enriched["smartSetup"]["verification"] = {"status": "rejected", "reason": "invalid_network_decision"}
            return enriched

        verification = extract_network_recipe(records, recipe)
        enriched["smartSetup"]["verification"] = verification.details
        if not verification.found:
            enriched["sourceType"] = "dom"
            enriched["smartSetup"]["status"] = "recipe_rejected"
            return enriched

        enriched["sourceType"] = "network"
        enriched["semanticType"] = selection.get("semanticType") or infer_semantic_type(recipe.get("valueLabel"))
        enriched["initialValue"] = verification.value
        enriched["networkRecipe"] = recipe
        enriched["smartSetup"]["status"] = "recipe_verified"
        return enriched


def build_default_smart_setup_service() -> SmartSetupService:
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return SmartSetupService(None)
    model = os.environ.get("OPENPULSE_GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    return SmartSetupService(GoogleAIStudioAdvisor(api_key=api_key, model=model))


def build_network_candidates(selection: dict[str, Any], records: list[NetworkRecord]) -> list[dict[str, Any]]:
    target_text = string_or_empty(selection.get("initialValue") or selection.get("targetText"))
    nearby_text = string_or_empty(selection.get("nearbyText"))
    combined_text = " ".join(part for part in [target_text, nearby_text] if part)
    click_tokens = tokenize(combined_text)
    scored: list[dict[str, Any]] = []

    for record in records:
        for hit in iter_scalar_hits(record.json_body):
            value_score = score_value_against_selection(hit.value, target_text, nearby_text)
            item_tokens = tokenize(item_text(hit.item))
            token_score = overlap_score(click_tokens, item_tokens)
            key_score = 0.18 if hit.key and VALUE_KEY_RE.search(hit.key) else 0
            score = min(1.0, (value_score * 0.66) + (token_score * 0.26) + key_score)
            if score < 0.18:
                continue
            relative_path = relative_json_path(hit.item_path, hit.path)
            identity_options = identity_options_for_item(hit.item, click_tokens, max_fields=10)
            if not relative_path:
                continue
            scored.append(
                {
                    "score": round(score, 3),
                    "record": record.metadata(),
                    "collectionPath": hit.collection_path,
                    "itemPath": hit.item_path,
                    "valuePath": relative_path,
                    "valueKey": hit.key,
                    "value": hit.value,
                    "identityOptions": identity_options,
                    "itemPreview": sanitize_json_preview(hit.item),
                    "_item": hit.item,
                }
            )

    scored.sort(
        key=lambda candidate: (
            candidate["score"],
            len(candidate["identityOptions"]),
            1 if VALUE_KEY_RE.search(string_or_empty(candidate.get("valueKey"))) else 0,
        ),
        reverse=True,
    )
    for index, candidate in enumerate(scored, start=1):
        candidate["candidateId"] = f"net-{index}"
    return scored[:MAX_CANDIDATES_FOR_ADVISOR]


def build_advisor_packet(selection: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "task": "choose_monitor_recipe",
        "clickedText": string_or_empty(selection.get("initialValue") or selection.get("targetText")),
        "semanticType": selection.get("semanticType"),
        "nearbyVisibleText": truncate(string_or_empty(selection.get("nearbyText")), 900),
        "domCandidate": {
            "source": "dom",
            "semanticType": selection.get("semanticType"),
            "initialValue": selection.get("initialValue"),
            "nearbyText": truncate(string_or_empty(selection.get("nearbyText")), 500),
            "selector": selection.get("selector"),
            "domPath": selection.get("domPath"),
        },
        "networkCandidates": [candidate_for_advisor(candidate) for candidate in candidates],
        "instructions": [
            "Choose network only when it clearly maps to the selected visible value.",
            "For repeated lists, prefer stable identity fields such as id/productId/serviceId/listingId/sku plus time/name when useful.",
            "Never rely on array index as identity.",
            "Choose dom for static text or when no network candidate is reliable.",
        ],
    }


def candidate_for_advisor(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidateId": candidate["candidateId"],
        "score": candidate["score"],
        "request": {
            "method": candidate["record"]["method"],
            "url": truncate(candidate["record"]["url"], 500),
            "status": candidate["record"]["status"],
        },
        "collectionPath": candidate["collectionPath"],
        "valuePath": candidate["valuePath"],
        "valueKey": candidate["valueKey"],
        "value": candidate["value"],
        "identityOptions": candidate["identityOptions"],
        "itemPreview": sanitize_json_preview(candidate.get("_item") or candidate.get("itemPreview"), max_depth=3),
    }


def recipe_from_decision(decision: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidate_id = decision.get("candidateId")
    candidate = next((item for item in candidates if item["candidateId"] == candidate_id), None)
    if candidate is None:
        return None
    item = candidate.get("_item")
    if not isinstance(item, dict):
        return None
    requested_fields = decision.get("identityFields")
    if not isinstance(requested_fields, list) or not requested_fields:
        requested_fields = [field["path"] for field in candidate.get("identityOptions", [])[:4]]
    identity: dict[str, Any] = {}
    for field in requested_fields:
        if not isinstance(field, str):
            continue
        value = get_relative_path(item, field)
        if is_good_identity_value(value):
            identity[field] = value
    if not identity:
        return None
    value_path = decision.get("valuePath") if isinstance(decision.get("valuePath"), str) else candidate["valuePath"]
    if get_relative_path(item, value_path) is None:
        return None
    record = candidate["record"]
    return {
        "type": "network_json",
        "request": {
            "method": record["method"],
            "url": record["url"],
            "urlPath": url_without_query(record["url"]),
            "postDataSha256": record.get("postDataSha256"),
        },
        "collectionPath": candidate["collectionPath"],
        "identity": identity,
        "valuePath": value_path,
        "valueLabel": decision.get("label") or candidate.get("valueKey") or "selected value",
        "confidence": decision.get("confidence"),
        "candidateId": candidate["candidateId"],
    }


def extract_network_recipe(records: list[NetworkRecord], recipe: dict[str, Any]) -> ExtractedValue:
    matching_records = [record for record in records if record_matches_recipe(record, recipe)]
    if not matching_records:
        return ExtractedValue(
            found=False,
            value=None,
            details={"status": "unverified", "reason": "network_response_not_seen"},
        )
    identity = recipe.get("identity")
    if not isinstance(identity, dict) or not identity:
        return ExtractedValue(found=False, value=None, details={"status": "unverified", "reason": "missing_identity"})

    for record in reversed(matching_records):
        items = list(iter_collection(record.json_body, string_or_empty(recipe.get("collectionPath"))))
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if not identity_matches(item, identity):
                continue
            value = get_relative_path(item, string_or_empty(recipe.get("valuePath")))
            if value is None:
                return ExtractedValue(
                    found=False,
                    value=None,
                    details={
                        "status": "unverified",
                        "reason": "value_path_missing",
                        "matchedIndex": index,
                        "record": record.metadata(),
                    },
                )
            return ExtractedValue(
                found=True,
                value=scalar_display(value),
                details={
                    "status": "verified",
                    "source": "network",
                    "strategy": "network_recipe",
                    "matchedIndex": index,
                    "collectionPath": recipe.get("collectionPath"),
                    "valuePath": recipe.get("valuePath"),
                    "record": record.metadata(),
                },
            )
    return ExtractedValue(
        found=False,
        value=None,
        details={
            "status": "unverified",
            "reason": "identity_not_found",
            "collectionPath": recipe.get("collectionPath"),
            "recordCount": len(matching_records),
        },
    )


def record_matches_recipe(record: NetworkRecord, recipe: dict[str, Any]) -> bool:
    request = recipe.get("request") if isinstance(recipe.get("request"), dict) else {}
    method = request.get("method")
    if method and string_or_empty(method).upper() != record.method.upper():
        return False
    url_path = request.get("urlPath")
    if url_path and url_without_query(record.url) != url_path:
        return False
    url = request.get("url")
    if url and not url_path and record.url != url:
        return False
    post_hash = request.get("postDataSha256")
    if post_hash and record.post_data_sha256 and post_hash != record.post_data_sha256:
        return False
    return True


def identity_matches(item: dict[str, Any], identity: dict[str, Any]) -> bool:
    for path, expected in identity.items():
        if get_relative_path(item, path) != expected:
            return False
    return True


def iter_scalar_hits(
    node: Any,
    *,
    path: str = "$",
    key: str | None = None,
    entity_item: dict[str, Any] | None = None,
    entity_path: str | None = None,
    collection_path: str | None = None,
) -> list[ScalarHit]:
    hits: list[ScalarHit] = []
    if isinstance(node, dict):
        for child_key, child in node.items():
            hits.extend(
                iter_scalar_hits(
                    child,
                    path=json_path_child(path, str(child_key)),
                    key=str(child_key),
                    entity_item=entity_item,
                    entity_path=entity_path,
                    collection_path=collection_path,
                )
            )
        return hits
    if isinstance(node, list):
        for index, child in enumerate(node):
            child_path = f"{path}[{index}]"
            child_entity = entity_item
            child_entity_path = entity_path
            child_collection_path = collection_path
            if isinstance(child, dict) and child_entity is None:
                child_entity = child
                child_entity_path = child_path
                child_collection_path = f"{path}[*]"
            hits.extend(
                iter_scalar_hits(
                    child,
                    path=child_path,
                    key=key,
                    entity_item=child_entity,
                    entity_path=child_entity_path,
                    collection_path=child_collection_path,
                )
            )
        return hits
    if is_scalar(node) and isinstance(entity_item, dict) and entity_path and collection_path:
        hits.append(
            ScalarHit(
                path=path,
                key=key,
                value=node,
                item_path=entity_path,
                collection_path=collection_path,
                item=entity_item,
            )
        )
    return hits


def iter_collection(json_body: Any, collection_path: str):
    if not collection_path.endswith("[*]"):
        return
    base_path = collection_path[:-3]
    collection = get_json_path(json_body, base_path)
    if isinstance(collection, list):
        yield from collection


def get_relative_path(item: dict[str, Any], path: str) -> Any:
    if not path:
        return None
    if path.startswith("$."):
        return get_json_path(item, path)
    if path.startswith("$["):
        return get_json_path(item, path)
    return get_json_path(item, f"$.{path}")


def get_json_path(value: Any, path: str) -> Any:
    if path in {"", "$"}:
        return value
    tokens = parse_json_path(path)
    current = value
    for token in tokens:
        if token == "*":
            return None
        if isinstance(token, int):
            if not isinstance(current, list) or token < 0 or token >= len(current):
                return None
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                return None
            current = current[token]
    return current


def parse_json_path(path: str) -> list[str | int]:
    if not path.startswith("$"):
        path = f"$.{path}"
    tokens: list[str | int] = []
    index = 1
    while index < len(path):
        char = path[index]
        if char == ".":
            index += 1
            start = index
            while index < len(path) and path[index] not in ".[":
                index += 1
            if start < index:
                tokens.append(path[start:index])
            continue
        if char == "[":
            end = path.find("]", index)
            if end < 0:
                return tokens
            raw = path[index + 1 : end]
            if raw == "*":
                tokens.append("*")
            elif raw.startswith(('"', "'")):
                with_context = raw if raw.startswith('"') else json.dumps(raw.strip("'"))
                tokens.append(json.loads(with_context))
            else:
                try:
                    tokens.append(int(raw))
                except ValueError:
                    return tokens
            index = end + 1
            continue
        index += 1
    return tokens


def relative_json_path(item_path: str, value_path: str) -> str | None:
    if value_path == item_path:
        return "$"
    if value_path.startswith(f"{item_path}."):
        return f"${value_path[len(item_path):]}"
    if value_path.startswith(f"{item_path}["):
        return f"${value_path[len(item_path):]}"
    return None


def json_path_child(path: str, key: str) -> str:
    if IDENTIFIER_RE.match(key):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key)}]"


def identity_options_for_item(item: dict[str, Any], click_tokens: set[str], *, max_fields: int) -> list[dict[str, Any]]:
    scored: list[tuple[float, str, Any]] = []
    for path, value in flatten_scalars(item):
        if not is_good_identity_value(value):
            continue
        key_score = identity_key_score(path)
        overlap = overlap_score(click_tokens, tokenize(f"{path} {scalar_display(value)}"))
        if key_score <= 0 and overlap <= 0:
            continue
        scored.append((key_score + overlap, path, value))
    scored.sort(key=lambda option: option[0], reverse=True)
    return [
        {
            "path": path,
            "value": redact_if_sensitive(path, value),
            "score": round(score, 3),
        }
        for score, path, value in scored[:max_fields]
    ]


def flatten_scalars(value: dict[str, Any], *, prefix: str = "", max_depth: int = 2) -> list[tuple[str, Any]]:
    fields: list[tuple[str, Any]] = []
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if is_scalar(child):
            fields.append((path, child))
        elif isinstance(child, dict) and max_depth > 1:
            fields.extend(flatten_scalars(child, prefix=path, max_depth=max_depth - 1))
    return fields


def identity_key_score(path: str) -> float:
    tail = path.rsplit(".", 1)[-1]
    if ID_KEY_RE.search(tail):
        return 1.0
    if IDENTITY_HINT_RE.search(tail):
        return 0.55
    return 0.0


def score_value_against_selection(value: Any, target_text: str, nearby_text: str) -> float:
    value_text = scalar_display(value)
    if not value_text:
        return 0.0
    target_numbers = numbers_from_text(target_text)
    value_number = number_from_any(value)
    if target_numbers and value_number is not None:
        return 1.0 if any(abs(value_number - number) < 0.0001 for number in target_numbers) else 0.0
    normalized_value = normalize_text(value_text)
    normalized_target = normalize_text(target_text)
    normalized_nearby = normalize_text(nearby_text)
    if normalized_value and normalized_target and normalized_value == normalized_target:
        return 1.0
    if normalized_value and normalized_nearby and normalized_value in normalized_nearby:
        return 0.62
    return 0.0


def number_from_any(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    numbers = numbers_from_text(str(value))
    return numbers[0] if len(numbers) == 1 else None


def numbers_from_text(text: str) -> list[float]:
    numbers: list[float] = []
    for match in NUMBER_RE.finditer(string_or_empty(text)):
        try:
            numbers.append(float(match.group(0).replace(",", "")))
        except ValueError:
            continue
    return numbers


def overlap_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, min(len(left), len(right)))


def tokenize(text: Any) -> set[str]:
    return {match.group(0).lower() for match in TOKEN_RE.finditer(string_or_empty(text)) if len(match.group(0)) >= 2}


def item_text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in flatten_scalars(item, max_depth=2):
        parts.append(key)
        parts.append(scalar_display(value))
    return " ".join(parts)


def scalar_display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def is_good_identity_value(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    text = scalar_display(value)
    return bool(text) and len(text) <= 140


def infer_semantic_type(label: Any) -> str:
    text = string_or_empty(label).lower()
    if any(term in text for term in ("price", "fare", "amount", "cost")):
        return "price"
    if "status" in text:
        return "status"
    return "text"


def sanitize_json_preview(value: Any, *, max_depth: int = 2, max_items: int = 16) -> Any:
    if max_depth < 0:
        return "..."
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= max_items:
                result["..."] = f"{len(value) - max_items} more keys"
                break
            result[str(key)] = redact_if_sensitive(str(key), sanitize_json_preview(child, max_depth=max_depth - 1))
        return result
    if isinstance(value, list):
        items = [sanitize_json_preview(child, max_depth=max_depth - 1) for child in value[:max_items]]
        if len(value) > max_items:
            items.append(f"... {len(value) - max_items} more items")
        return items
    if isinstance(value, str):
        return truncate(value, 300)
    return value


def redact_if_sensitive(key: str, value: Any) -> Any:
    if SENSITIVE_KEY_RE.search(key):
        return "[redacted]"
    return value


def normalize_text(text: Any) -> str:
    return " ".join(string_or_empty(text).lower().replace(",", "").split())


def string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else f"{value[:limit]}..."


def url_without_query(url: str) -> str:
    return url.split("?", 1)[0]


def scalar_count(json_body: Any) -> int:
    return len(
        [
            value
            for value in iter_all_scalars(json_body)
        ]
    )


def iter_all_scalars(value: Any):
    if isinstance(value, dict):
        for child in value.values():
            yield from iter_all_scalars(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_all_scalars(child)
    elif is_scalar(value):
        yield value


async def network_record_from_response(response: Any, *, sequence: int, captured_at: str) -> NetworkRecord | None:
    request = response.request
    resource_type = string_or_empty(getattr(request, "resource_type", ""))
    if resource_type and resource_type not in {"fetch", "xhr"}:
        return None
    headers = getattr(response, "headers", {}) or {}
    content_type = string_or_empty(headers.get("content-type") or headers.get("Content-Type"))
    url = string_or_empty(getattr(response, "url", ""))
    if "json" not in content_type.lower() and "/api/" not in url:
        return None
    try:
        text = await response.text()
    except Exception:
        return None
    if len(text) > 1_500_000:
        return None
    try:
        json_body = json.loads(text)
    except json.JSONDecodeError:
        return None
    post_data = getattr(request, "post_data", None)
    if callable(post_data):
        post_data = post_data()
    post_preview = truncate(post_data, 600) if isinstance(post_data, str) else None
    post_hash = hashlib.sha256(post_data.encode()).hexdigest() if isinstance(post_data, str) else None
    return NetworkRecord(
        sequence=sequence,
        captured_at=captured_at,
        url=url,
        method=string_or_empty(getattr(request, "method", "")).upper() or "GET",
        status=int(getattr(response, "status", 0) or 0),
        resource_type=resource_type,
        content_type=content_type,
        post_data_sha256=post_hash,
        post_data_preview=post_preview,
        json_body=json_body,
        scalar_count=scalar_count(json_body),
    )


class GoogleAIStudioAdvisor:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        timeout_seconds: int = 30,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ):
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.urlopen = urlopen

    async def choose_recipe(self, packet: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.choose_recipe_sync, packet)

    def choose_recipe_sync(self, packet: dict[str, Any]) -> dict[str, Any]:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{self.model}:generateContent?key={self.api_key}"
        )
        body = json.dumps(self._request_body(packet)).encode()
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode())
        text = (
            payload.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "{}")
        )
        decision = parse_json_object_text(text)
        if not isinstance(decision, dict):
            raise ValueError("Gemini decision was not a JSON object")
        return decision

    def _request_body(self, packet: dict[str, Any]) -> dict[str, Any]:
        return {
            "contents": [
                {
                    "parts": [
                        {
                            "text": (
                                "Choose the safest OpenPulse monitor recipe. "
                                "Return only JSON matching the schema. Input:\n"
                                f"{json.dumps(packet, ensure_ascii=False)}"
                            )
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "response_mime_type": "application/json",
                "response_schema": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "enum": ["network", "dom"]},
                        "candidateId": {"type": "string"},
                        "identityFields": {"type": "array", "items": {"type": "string"}},
                        "valuePath": {"type": "string"},
                        "label": {"type": "string"},
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["source", "label", "confidence"],
                },
            },
        }


def parse_json_object_text(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = _decode_first_json_object(text)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def _decode_first_json_object(text: str) -> Any:
    decoder = json.JSONDecoder()
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found in Gemini response")
    value, _end = decoder.raw_decode(text[start:])
    return value
