from orchestrator.providers.agy import parse_models as parse_agy
from orchestrator.providers.codex import parse_models as parse_codex
import json


def test_agy_parses_tabular_model_ids():
    output="Fetching available models...\ngemini-3.8-flash-high\tGemini 3.8 Flash (High)\nclaude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n"
    assert parse_agy(output)==["gemini-3.8-flash-high","claude-sonnet-4-6"]


def test_codex_parses_catalog_and_filters_hidden_or_unsupported():
    output=json.dumps({"models":[
        {"slug":"gpt-6-astra","visibility":"list","supported_in_api":True},
        {"slug":"gpt-6-luna","visibility":"list","supported_in_api":True},
        {"slug":"hidden","visibility":"hidden","supported_in_api":True},
        {"slug":"unsupported","visibility":"list","supported_in_api":False},
    ]})
    assert parse_codex(output)==["gpt-6-astra","gpt-6-luna"]


def test_opencode_listing_parser_ignores_status_lines():
    import re
    output="opencode/big-pickle\nopencode-go/gpt-6-luna\nModels cache refreshed"
    assert re.findall(r"(?m)^\s*([\w.-]+/[\w.-]+)\s*$",output)==["opencode/big-pickle","opencode-go/gpt-6-luna"]
