from orchestrator.validation.parser import parse_validation


def test_parser_extracts_fenced_json():
    result=parse_validation('analysis\n```json\n{"status":"PASS","issues":[],"summary":"ok"}\n```','validator')
    assert result.status=="PASS" and result.summary=="ok"


def test_parser_blocks_prose_without_json():
    assert parse_validation("looks good", "validator").status=="PARSE_ERROR"


def test_parser_blocks_malformed_issue():
    assert parse_validation('{"status":"PASS","issues":[{"id":"1"}]}',"v").status=="PARSE_ERROR"
