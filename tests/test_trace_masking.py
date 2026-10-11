"""Nothing secret may leave the machine in a neatlogs span: inputs/outputs can hold the settings
object (keys, tokens) because traced functions receive the engine context."""

from __future__ import annotations

import json

from mergemind import tracing
from mergemind.config import Settings

NV = "nvapi-" + "Q2w9E8r7T6y5U4i3O2p1A0s9D8f7G6h5J4k3L2z1X0c9"
PAT = "github_pat_" + "11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRS"
NEAT = "Pr4zzsliBJ-" + "BpdI16TkT0kxxd19R5ACcQz"


def _fresh_scanner(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", NV)
    monkeypatch.setenv("GITHUB_TOKEN", PAT)
    monkeypatch.setenv("NEATLOGS_API_KEY", NEAT)
    tracing._scanner_cache.clear()


def test_settings_never_print_or_serialize_secrets():
    s = Settings(nvidia_api_key=NV, github_token=PAT, neatlogs_api_key=NEAT)
    for text in (repr(s), str(s), s.model_dump_json(), json.dumps(s.model_dump(mode="json"))):
        assert NV not in text and PAT not in text and NEAT not in text
    assert s.github_token == PAT  # the value itself is still usable


def test_mask_scrubs_inputs_outputs_and_attributes(monkeypatch):
    _fresh_scanner(monkeypatch)
    ctx_json = json.dumps({"ctx": {"settings": {"nvidia api key": NV, "github token": PAT, "llm_rpm": 30},
                                   "note": f"called with {NEAT}"}, "evt": {"kind": "comment"}})
    span = {"trace_id": "4bf92f3577b34da6a3ce929d0e0e4736", "name": "issue_event",
            "attributes": {"input.value": ctx_json, "output.value": "ok", "neatlogs.github_token": PAT,
                           "llm.headers": {"Authorization": "Bearer " + NV}, "neatlogs.issue": 7},
            "events": [{"message": f"retrying with {PAT}"}]}
    out = json.dumps(tracing.mask(span))
    assert NV not in out and PAT not in out and NEAT not in out
    masked = tracing.mask(span)
    assert masked["trace_id"] == span["trace_id"] and masked["attributes"]["neatlogs.issue"] == 7
    assert '"kind": "comment"' in masked["attributes"]["input.value"]  # useful data survives


def test_provider_tokens_are_scrubbed_even_if_not_in_the_environment(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    tracing._scanner_cache.clear()
    other = "ghp_" + "Zr8Kq2Lm9Nx4Vb7Ct1Wy5Ue3Pa6Sd0Fg2Hj4"
    assert other not in json.dumps(tracing.mask({"attributes": {"input.value": f"token {other}"}}))
