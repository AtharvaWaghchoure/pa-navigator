import json


def create_text_response(text: str, is_error: bool = False) -> str:
    if is_error:
        raise ValueError(text)
    return text


def create_json_response(data: dict, is_error: bool = False) -> str:
    if is_error:
        raise ValueError(json.dumps(data))
    return json.dumps(data, indent=2)
