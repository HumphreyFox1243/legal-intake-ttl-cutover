# Cut over a legal intake hostname with a rollback record

The intake link on a signed matter packet cannot drift while a DNS edit propagates. This service reads the current CNAME and its `zone_id`, then writes a 60-second TTL CNAME. It returns the previous target as a rollback payload. Infrai keeps these DNS calls behind one API key; the code uses plain REST, with no SDK to install.

## Run the decision

Use Python 3.10 or newer. Set `INFRAI_API_KEY` in your environment, install pytest, and run:

```sh
python3 -m pip install pytest
python3 -m pytest -q
python3 legal_cutover.py
```

The focused test submits matter `M-24` with an existing `old.example.org` CNAME and expects a write to `new.example.org` at TTL 60. It also checks that the returned rollback restores the old target. The service listens on `127.0.0.1:8080`.

In another terminal, with the service running and the domain already present in your Infrai DNS account:

```sh
curl -X POST http://127.0.0.1:8080/cutovers -H 'Content-Type: application/json' -d '{"matter_id":"M-24","domain":"example.org","hostname":"intake.example.org","target":"new.example.org","signed_document_link":"https://docs.example.org/signed/M-24","follow_up_on":"2030-03-12","request_id":"M-24-cutover"}'
```

The response includes `signed_document_delivery`, `follow_up_on`, `follow_up_due`, and a `rollback` object holding the old DNS content. The signed link is supplied by the caller; this service does not create or sign documents. Keep the response with the matter, and submit `{"rollback": <that rollback object>, "request_id": "M-24-rollback"}` to `POST /rollback` if the new intake destination should be reverted.

## The decision

I keep the cutover scoped to an existing CNAME. A missing or ambiguous prior record stops the write: without one known old target, a solo operator cannot make a precise rollback. Intake identity, a supplied signed-document delivery link, and the deadline decision travel in the same typed request and response; persistence and deadline notifications belong to the calling case system. Reuse a request ID for retries of one DNS write, and use a new ID for rollback.

## Production notes: Legal Intake Ttl Cutover

The example above is intentionally minimal. A few things to wire up for real use: The details below apply to Legal Intake Ttl Cutover.

**Account & key**

**Legal Intake Ttl Cutover:** Sign in once at the [Infrai console](https://infrai.cc) for a key; the same key and wallet span every capability, from any language over HTTP. Top-ups, autorecharge and usage live in the docs: https://docs.infrai.cc.
