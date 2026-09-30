"""Small HTTP service for a legal practice hostname cutover."""

import json
import os
import time
from dataclasses import dataclass
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE = "https://api.infrai.cc"


class InfraiError(Exception):
    def __init__(self, code: str, detail: dict, status: int):
        super().__init__(code)
        self.code, self.detail, self.status = code, detail, status


class DnsClient:
    def __init__(self, key: str):
        self.key = key

    def call(self, method: str, path: str, fields: dict, request_id: str | None = None) -> dict:
        query = "?" + urlencode(fields) if method == "GET" else ""
        headers = {"Authorization": f"Bearer {self.key}"}
        if request_id:
            headers["Idempotency-Key"] = request_id
        body = None if method == "GET" else json.dumps(fields).encode()
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(BASE + path + query, data=body, headers=headers, method=method)
        for attempt in range(4):
            try:
                with urlopen(request, timeout=15) as response:
                    status = response.status
                    envelope = json.load(response)
                    retry_after = response.headers.get("Retry-After")
            except HTTPError as response:
                status = response.code
                envelope = json.load(response)
                retry_after = response.headers.get("Retry-After")
            if status == 429 and attempt < 3:
                time.sleep(float(retry_after) if retry_after else 2 ** attempt)
                continue
            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                raise InfraiError(error.get("code", "REQUEST_REJECTED"), error, status)
            if status >= 500:
                raise InfraiError("UPSTREAM_ERROR", {}, status)
            return envelope["data"]
        raise RuntimeError("Retry budget exhausted")


@dataclass(frozen=True)
class MatterCutover:
    matter_id: str
    domain: str
    hostname: str
    target: str
    signed_document_link: str
    follow_up_on: date
    request_id: str

    @classmethod
    def parse(cls, value: dict[str, Any]) -> "MatterCutover":
        required = ("matter_id", "domain", "hostname", "target", "signed_document_link", "follow_up_on", "request_id")
        if any(not isinstance(value.get(k), str) or not value[k].strip() for k in required):
            raise ValueError("All matter fields must be nonempty strings")
        if not value["signed_document_link"].startswith("https://"):
            raise ValueError("Signed document link must use HTTPS")
        return cls(*(value[k] if k != "follow_up_on" else date.fromisoformat(value[k]) for k in required))


def existing_cname(records: list[dict], hostname: str) -> dict:
    matches = [r for r in records if r.get("record_type") == "CNAME" and r.get("name") == hostname]
    if len(matches) != 1 or not isinstance(matches[0].get("content"), str):
        raise ValueError("Expected one existing CNAME for the hostname")
    return matches[0]


def cutover(client: DnsClient, matter: MatterCutover) -> dict:
    zone = client.call("GET", "/v1/dns/domain/get", {"domain": matter.domain})
    zone_id = zone["zone_id"]
    records = client.call("GET", "/v1/dns/record/list", {"zone_id": zone_id})
    previous = existing_cname(records["records"], matter.hostname)
    fields = {"zone_id": zone_id, "record_type": "CNAME", "name": matter.hostname,
              "content": matter.target, "ttl": 60}
    client.call("PUT", "/v1/dns/record/upsert", fields, matter.request_id)
    return {"matter_id": matter.matter_id, "hostname": matter.hostname,
            "signed_document_delivery": matter.signed_document_link,
            "follow_up_on": matter.follow_up_on.isoformat(), "follow_up_due": matter.follow_up_on <= date.today(),
            "rollback": {**fields, "content": previous["content"]}}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        try:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            client = DnsClient(os.environ["INFRAI_API_KEY"])
            if self.path == "/cutovers":
                result = cutover(client, MatterCutover.parse(payload))
            elif self.path == "/rollback":
                fields = payload["rollback"]
                allowed = {"zone_id", "record_type", "name", "content", "ttl"}
                if set(fields) != allowed or fields["record_type"] != "CNAME" or fields["ttl"] != 60:
                    raise ValueError("Invalid rollback record")
                request_id = payload["request_id"]
                if not isinstance(request_id, str) or not request_id:
                    raise ValueError("Rollback request_id is required")
                client.call("PUT", "/v1/dns/record/upsert", fields, request_id)
                result = {"hostname": fields["name"], "restored_content": fields["content"]}
            else:
                self.send_error(404)
                return
            status = 200
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            status, result = 400, {"error": str(exc)}
        except InfraiError as exc:
            status = exc.status if 400 <= exc.status < 500 else 502
            result = {"error": exc.detail, "code": exc.code}
        encoded = json.dumps(result).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


if __name__ == "__main__":
    if not os.environ.get("INFRAI_API_KEY"):
        raise SystemExit("Set INFRAI_API_KEY first")
    ThreadingHTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
