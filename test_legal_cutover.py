from datetime import date

from legal_cutover import MatterCutover, cutover


class FakeDns:
    def __init__(self):
        self.calls = []

    def call(self, method, path, fields, request_id=None):
        self.calls.append((method, path, fields, request_id))
        if path.endswith("/domain/get"):
            return {"zone_id": "zone-17"}
        if path.endswith("/record/list"):
            return {"records": [
                {"record_type": "CNAME", "name": "intake.example.org", "content": "old.example.org"}
            ]}
        return {}


def test_cutover_keeps_previous_target_for_rollback():
    client = FakeDns()
    matter = MatterCutover("M-24", "example.org", "intake.example.org",
                           "new.example.org", "https://docs.example.org/signed/M-24",
                           date(2030, 3, 12), "M-24-cutover")
    result = cutover(client, matter)
    assert client.calls[0][2] == {"domain": "example.org"}
    assert client.calls[1][2] == {"zone_id": "zone-17"}
    assert client.calls[2] == ("PUT", "/v1/dns/record/upsert",
                               {"zone_id": "zone-17", "record_type": "CNAME", "name": "intake.example.org",
                                "content": "new.example.org", "ttl": 60}, "M-24-cutover")
    assert result["rollback"]["content"] == "old.example.org"
    assert result["follow_up_due"] is False
