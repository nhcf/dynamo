    data: dict[str, Any] = {"nextn": nextn, "source": "backend_config"}
    method = spec.get("method")
    if method:
        data["method"] = str(method)
    return data
